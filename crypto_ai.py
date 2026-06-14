#!/usr/bin/env python3
"""
Crypto Trading Bot – Single-File, No Config.json
All settings are directly in the SETTINGS dictionary below.
Secrets must be set as environment variables: TELEGRAM_TOKEN, CHAT_ID, GROQ_API_KEY.
"""

import requests, json, os, sys, traceback, re, time, argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ===================== ALL SETTINGS – EDIT HERE =====================
SETTINGS = {
    # --- Essentials ---
    "initial_balance": 1000.0,          # starting USDT balance
    "daily_loss_limit_pct": 5.0,        # stop trading after losing X% of current balance in a day
    "risk_per_trade": 0.01,             # risk 1% of balance per trade
    "max_positions": 2,                 # maximum simultaneous open trades

    # --- Strategy thresholds ---
    "conviction_threshold": 1.49,       # minimum absolute conviction score to take a trade
    "ai_confidence_min": 4,             # minimum AI confidence (1-10) required
    "volatility_cap_pct": 7,            # ignore coins with 4h ATR > X% of price
    "atr_period": 14,
    "atr_stop_multiplier": 1.5,        # stop distance = max(ATR*mult, 1% of entry)

    # --- Data & Universe ---
    "binance_base_url": "https://api.binance.com",
    "universe_top_n": 30,               # number of top-volume USDT pairs to scan
    "min_notional": 10.0,               # minimum order value in USDT

    # --- Backtest (skeleton) ---
    "backtest_start": "2023-01-01",
    "backtest_end": "2024-01-01",
    "backtest_interval": "4h",
    "backtest_initial_balance": 1000.0,
}

# ===================== SECRETS (set as env vars) =====================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

if not TELEGRAM_TOKEN or not CHAT_ID:
    print("Warning: TELEGRAM_TOKEN and CHAT_ID not set – bot will not send messages.")

# Shortcuts to settings
PORTFOLIO_FILE = "portfolio.json"
TRADE_LOG_CSV = "trade_log.csv"
OPEN_TRADES_CSV = "open_trades.csv"
TRADE_RESULTS_CSV = "trade_results.csv"

# ===================== PERSISTENT PORTFOLIO =====================
def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE) as f:
                data = json.load(f)
            return {
                "balance_usdt": data.get("balance_usdt", SETTINGS["initial_balance"]),
                "realized_pnl": data.get("realized_pnl", 0.0),
                "open_positions": data.get("open_positions", 0)
            }
        except:
            pass
    return {
        "balance_usdt": SETTINGS["initial_balance"],
        "realized_pnl": 0.0,
        "open_positions": 0
    }

def save_portfolio(p):
    try:
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(p, f, indent=2)
    except:
        print("Warning: Could not save portfolio.json")

# ===================== BINANCE DATA HELPERS =====================
def binance_klines(symbol, interval, start_time=None, end_time=None, limit=500):
    url = f"{SETTINGS['binance_base_url']}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time:
        if isinstance(start_time, datetime):
            params["startTime"] = int(start_time.timestamp() * 1000)
        else:
            params["startTime"] = start_time
    if end_time:
        if isinstance(end_time, datetime):
            params["endTime"] = int(end_time.timestamp() * 1000)
        else:
            params["endTime"] = end_time

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data, columns=[
                "timestamp", "Open", "High", "Low", "Close", "Volume",
                "close_time", "quote_asset_volume", "number_of_trades",
                "taker_buy_base", "taker_buy_quote", "ignore"
            ])
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit='ms')
            df.set_index("timestamp", inplace=True)
            return df
        else:
            print(f"Binance klines error {resp.status_code}: {resp.text}")
            return pd.DataFrame()
    except Exception as e:
        print(f"Binance klines exception: {e}")
        return pd.DataFrame()

def binance_24hr_tickers():
    url = f"{SETTINGS['binance_base_url']}/api/v3/ticker/24hr"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            usdt_pairs = [t for t in data if t["symbol"].endswith("USDT")]
            for t in usdt_pairs:
                t["quoteVolume"] = float(t["quoteVolume"])
            usdt_pairs.sort(key=lambda x: x["quoteVolume"], reverse=True)
            return usdt_pairs
        else:
            print("Binance ticker error")
            return []
    except Exception as e:
        print(f"Binance ticker exception: {e}")
        return []

def binance_exchange_info(symbol):
    url = f"{SETTINGS['binance_base_url']}/api/v3/exchangeInfo"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for s in data["symbols"]:
                if s["symbol"] == symbol:
                    filters = {f["filterType"]: f for f in s["filters"]}
                    step_size = float(filters["LOT_SIZE"]["stepSize"])
                    min_qty = float(filters["LOT_SIZE"]["minQty"])
                    min_notional = float(filters.get("MIN_NOTIONAL", {"minNotional": "10"})["minNotional"])
                    return step_size, min_qty, min_notional
    except:
        pass
    return 0.001, 0.001, 10.0

# ===================== CSV LOGGING =====================
def init_csv(filepath, columns):
    if not os.path.exists(filepath):
        pd.DataFrame(columns=columns).to_csv(filepath, index=False)

def append_csv(filepath, df_new):
    try:
        existing = pd.read_csv(filepath)
        updated = pd.concat([existing, df_new], ignore_index=True)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        updated = df_new
    updated.to_csv(filepath, index=False)

def save_csv(filepath, df):
    df.to_csv(filepath, index=False)

def initialize_trade_files():
    init_csv(TRADE_LOG_CSV, ["timestamp", "symbol", "action", "entry", "stop",
                             "TP1", "TP2", "TP3", "TP4", "TP5", "conviction", "ai_confidence"])
    init_csv(OPEN_TRADES_CSV, ["timestamp", "symbol", "action", "entry", "stop",
                               "TP1", "TP2", "TP3", "TP4", "TP5", "status", "quantity", "highest_tp"])
    init_csv(TRADE_RESULTS_CSV, ["timestamp", "symbol", "action", "entry", "stop",
                                 "TP1", "TP2", "TP3", "TP4", "TP5", "status", "hit_level",
                                 "close_time", "exit_price", "quantity", "pnl_usdt"])

def log_signal(signal):
    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": signal["symbol"],
        "action": signal["action"],
        "entry": signal["limit_price"],
        "stop": signal["stop_loss"],
        "TP1": signal["take_profits"][0],
        "TP2": signal["take_profits"][1],
        "TP3": signal["take_profits"][2],
        "TP4": signal["take_profits"][3],
        "TP5": signal["take_profits"][4],
        "conviction": signal["conviction_score"],
        "ai_confidence": signal["confidence_score"],
    }
    append_csv(TRADE_LOG_CSV, pd.DataFrame([row]))

def add_open_trade(signal):
    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": signal["symbol"],
        "action": signal["action"],
        "entry": signal["limit_price"],
        "stop": signal["stop_loss"],
        "TP1": signal["take_profits"][0],
        "TP2": signal["take_profits"][1],
        "TP3": signal["take_profits"][2],
        "TP4": signal["take_profits"][3],
        "TP5": signal["take_profits"][4],
        "status": "open",
        "quantity": signal["quantity"],
        "highest_tp": -1
    }
    append_csv(OPEN_TRADES_CSV, pd.DataFrame([row]))

# ===================== TECHNICAL ANALYSIS =====================
def get_technicals(df):
    if df.empty or len(df) < 50:
        return {"trend": 0, "adx": 0, "structure": 0, "combined": 0, "ema50_distance": 1.0, "error": "insufficient data"}
    closes = df['Close']
    highs = df['High']
    lows = df['Low']
    ema50 = closes.ewm(span=50, adjust=False).mean()
    ema200 = closes.ewm(span=200, adjust=False).mean() if len(closes) >= 200 else ema50
    current = closes.iloc[-1]
    trend = 0
    if current > ema50.iloc[-1]:
        trend += 1.5
    else:
        trend -= 1.5
    if ema50.iloc[-1] > ema200.iloc[-1]:
        trend += 1.5
    else:
        trend -= 1.5
    trend = max(-3, min(3, trend))

    def calc_adx(high, low, close, period=14):
        dm_plus = high.diff()
        dm_minus = -low.diff()
        dm_plus[dm_plus < 0] = 0
        dm_minus[dm_minus < 0] = 0
        tr = pd.concat([high - low,
                        (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1/period, adjust=False).mean()
        di_plus = 100 * (dm_plus.ewm(alpha=1/period, adjust=False).mean() / atr)
        di_minus = 100 * (dm_minus.ewm(alpha=1/period, adjust=False).mean() / atr)
        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
        adx = dx.ewm(alpha=1/period, adjust=False).mean()
        return adx, di_plus, di_minus

    adx_series, di_plus, di_minus = calc_adx(highs, lows, closes, SETTINGS["atr_period"])
    adx_now = adx_series.iloc[-1]
    di_plus_now = di_plus.iloc[-1]
    di_minus_now = di_minus.iloc[-1]
    adx_score = 0
    if adx_now > 25:
        if di_plus_now > di_minus_now:
            adx_score = 2.5
        else:
            adx_score = -2.5
    elif adx_now > 20:
        if di_plus_now > di_minus_now:
            adx_score = 1.0
        else:
            adx_score = -1.0

    window = 7
    lookback = min(50, len(highs))
    recent_highs = highs.iloc[-lookback:]
    recent_lows = lows.iloc[-lookback:]
    swing_highs = []
    swing_lows = []
    for i in range(window, len(recent_highs) - window):
        if all(recent_highs.iloc[i] >= recent_highs.iloc[i-window:i+window+1]):
            swing_highs.append((i, recent_highs.iloc[i]))
        if all(recent_lows.iloc[i] <= recent_lows.iloc[i-window:i+window+1]):
            swing_lows.append((i, recent_lows.iloc[i]))

    structure_score = 0
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        last_hh = swing_highs[-1][1] > swing_highs[-2][1]
        last_hl = swing_lows[-1][1] > swing_lows[-2][1]
        if last_hh and last_hl:
            structure_score = 2.0 if len(swing_highs) < 3 else (3.0 if swing_highs[-2][1] > swing_highs[-3][1] else 2.0)
        elif (not last_hh) and (not last_hl):
            structure_score = -2.0 if len(swing_highs) < 3 else (-3.0 if swing_highs[-2][1] < swing_highs[-3][1] else -2.0)
    structure_score = max(-3, min(3, structure_score))

    combined = trend * 0.30 + adx_score * 0.25 + structure_score * 0.45
    ema50_dist = abs(current - ema50.iloc[-1]) / current
    trend_dir = "up" if current > ema50.iloc[-1] else "down"
    return {
        "trend": trend, "adx": adx_score, "structure": structure_score,
        "combined": combined, "ema50_distance": ema50_dist,
        "adx_value": adx_now, "trend_dir": trend_dir, "error": None
    }

def get_4h_atr(df, current_price):
    if df.empty or len(df) < SETTINGS["atr_period"]:
        return current_price * 0.02
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(SETTINGS["atr_period"]).mean().iloc[-1]
    if pd.isna(atr):
        return current_price * 0.02
    return atr

def refined_buying_pressure(df):
    if df.empty or len(df) < 48:
        return 0, 0
    short = df.tail(12)
    buy_vol_s = short.loc[short['Close'] > short['Open'], 'Volume'].sum()
    sell_vol_s = short.loc[short['Close'] <= short['Open'], 'Volume'].sum()
    total_s = buy_vol_s + sell_vol_s
    short_press = (buy_vol_s - sell_vol_s) / total_s if total_s > 0 else 0

    long_df = df.tail(48)
    buy_vol_l = long_df.loc[long_df['Close'] > long_df['Open'], 'Volume'].sum()
    sell_vol_l = long_df.loc[long_df['Close'] <= long_df['Open'], 'Volume'].sum()
    total_l = buy_vol_l + sell_vol_l
    long_press = (buy_vol_l - sell_vol_l) / total_l if total_l > 0 else 0
    return short_press, long_press

def get_buying_pressure(df):
    short_p, long_p = refined_buying_pressure(df)
    if short_p * long_p > 0:
        score = (short_p + long_p) / 2 * 3
    else:
        score = (short_p + long_p) / 2 * 3 * 0.3
    return score

def anchored_vwap_score(df, current_price):
    if len(df) < 50:
        return 0
    typical = (df['High'] + df['Low'] + df['Close']) / 3
    vpv = typical * df['Volume']
    total_vol = df['Volume'].sum()
    if total_vol == 0:
        return 0
    vwap = vpv.sum() / total_vol
    deviation = (current_price - vwap) / vwap * 100
    if deviation > 1:
        return 1
    elif deviation < -1:
        return -1
    return 0

def get_volatility_score(df, current_price):
    atr = get_4h_atr(df, current_price)
    atr_pct = atr / current_price * 100
    if atr_pct < 2 or atr_pct > SETTINGS["volatility_cap_pct"]:
        return -1
    return 1

def btc_trend_score(df_btc):
    if df_btc.empty or len(df_btc) < 50:
        return 0
    closes = df_btc['Close']
    ema50 = closes.ewm(span=50, adjust=False).mean()
    current = closes.iloc[-1]
    return 2 if current > ema50.iloc[-1] else -2

def volume_trend_score(df, direction=None):
    if df.empty or len(df) < 12:
        return 0
    recent = df['Volume'].tail(6)
    first_half = recent[:3].mean()
    second_half = recent[3:].mean()
    if second_half > first_half * 1.05:
        return -2 if direction == "down" else 2
    elif second_half < first_half * 0.95:
        return -2 if direction == "up" else -2
    return 0

def institutional_macro_filter(df_btc):
    if df_btc.empty or len(df_btc) < 50:
        return 0
    closes = df_btc['Close']
    ema50 = closes.ewm(span=50, adjust=False).mean()
    btc_bullish = closes.iloc[-1] > ema50.iloc[-1]
    usdt_dom = 5.0  # fallback (no external API)
    macro = 0
    if btc_bullish:
        macro += 1
        if usdt_dom < 4.5:
            macro += 1
    else:
        macro -= 1
        if usdt_dom > 6.5:
            macro -= 1
    return max(-2, min(2, macro))

def trend_strength_bonus(adx_value, base_score):
    if adx_value > 35 and abs(base_score) > 0.5:
        return 0.30 * (1 if base_score > 0 else -1)
    elif adx_value > 30 and abs(base_score) > 0.5:
        return 0.20 * (1 if base_score > 0 else -1)
    return 0.0

def momentum_alignment_score(df, direction, layers):
    if df.empty or len(df) < 2:
        return 0.0
    last = df.iloc[-1]
    candle_agrees = (direction == "LONG" and last['Close'] > last['Open']) or \
                    (direction == "SHORT" and last['Close'] < last['Open'])
    if not candle_agrees:
        return 0.0
    supporting = 0
    if direction == "LONG":
        if layers.get("buying_press", 0) > 0.5: supporting += 1
        if layers.get("intermarket", 0) > 0.5: supporting += 1
        if layers.get("volume_trend", 0) > 0.5: supporting += 1
    else:
        if layers.get("buying_press", 0) < -0.5: supporting += 1
        if layers.get("intermarket", 0) < -0.5: supporting += 1
        if layers.get("volume_trend", 0) < -0.5: supporting += 1
    if supporting >= 2:
        return 0.20 if direction == "LONG" else -0.20
    return 0.0

# ========== SCORING ENGINE ==========
def score_coin(symbol, price, df_4h, df_btc, macro_score):
    errors = []
    tech = get_technicals(df_4h)
    if tech.get("error"):
        errors.append(f"tech: {tech['error']}")
    tech_combined = tech["combined"]
    ema50_distance = tech["ema50_distance"]
    adx_value = tech.get("adx_value", 0)
    trend_dir = tech.get("trend_dir", "up")

    buying_score = get_buying_pressure(df_4h)
    vol_score = get_volatility_score(df_4h, price)
    intermarket_s = btc_trend_score(df_btc)
    vol_trend_s = volume_trend_score(df_4h, direction=trend_dir)
    vwap_score = anchored_vwap_score(df_4h, price)

    total = (
        0.20 * tech_combined +
        0.45 * buying_score +
        0.05 * vol_score +
        0.25 * intermarket_s +
        0.05 * vol_trend_s
    )
    macro_multiplier = 1 + 0.15 * macro_score
    total *= macro_multiplier
    total += vwap_score * 0.1

    layers = {
        "tech": tech_combined,
        "buying_press": buying_score,
        "volatility": vol_score,
        "intermarket": intermarket_s,
        "volume_trend": vol_trend_s,
    }
    return max(-3, min(3, total)), layers, ema50_distance, adx_value, trend_dir, errors

# ========== AI REASONING (GROQ) ==========
def call_groq_reasoning(symbol, entry, atr, layers, errors=None):
    if not GROQ_API_KEY:
        return 5, "Market structure: neutral. Catalyst: none. Risk note: AI key missing."
    layer_str = "; ".join([f"{k}={v:.2f}" for k,v in layers.items()])
    err_str = "; ".join(errors) if errors else ""
    directional_scores = [layers["tech"], layers["buying_press"], layers["intermarket"], layers["volume_trend"]]
    bearish_count = sum(1 for s in directional_scores if s < -0.5)
    bullish_count = sum(1 for s in directional_scores if s > 0.5)
    alignment_strength = max(bearish_count, bullish_count)

    system_msg = (
        "You are a senior institutional crypto trader. "
        "Given a trade signal, write a concise market note in three short parts:\n"
        "- Market structure: what is happening on the chart\n"
        "- Catalyst: what is driving the move\n"
        "- Risk note: key invalidation or warning\n"
        "Use plain sentences, no numbers, no 'layers'. Under 150 characters. "
        "Return confidence 1-10 (10 = highest) and the note."
    )
    user_prompt = (
        f"Trade signal for {symbol} at {entry}. 4h ATR: {atr:.4f}. "
        f"Internal metrics: {layer_str}{err_str}. "
        f"Alignment: {alignment_strength}/4 metrics strongly aligned. "
        "Provide confidence (1-10) and the three-part note after '|'."
    )
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.6,
        "max_tokens": 150
    }
    try:
        resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"]
            conf_match = re.search(r'confidence[:\s]*(\d+)', text, re.IGNORECASE)
            conf = int(conf_match.group(1)) if conf_match else 5
            conf = max(1, min(10, conf))
            note = text.split("|", 1)[1].strip() if "|" in text else text
            return conf, note
    except:
        pass
    return 5, "Market structure: neutral. Catalyst: none. Risk note: automated signal."

# ========== TRAILING STOP & TRADE MANAGEMENT ==========
def update_stop(direction, tp_index, entry, tps):
    if direction == "LONG":
        return entry if tp_index == 0 else tps[tp_index - 1]
    else:
        return entry if tp_index == 0 else tps[tp_index - 1]

def resolve_ambiguous(sym, direction, entry, current_stop, tps, start_highest,
                      start_time, end_time, depth=0):
    if depth == 0:
        interval = '15m'
    elif depth == 1:
        interval = '5m'
    else:
        return resolve_heuristic(sym, direction, entry, current_stop, tps, start_highest,
                                 start_time, end_time)
    df = binance_klines(sym, interval, start_time=start_time, end_time=end_time)
    if df.empty:
        return resolve_heuristic(sym, direction, entry, current_stop, tps, start_highest,
                                 start_time, end_time)

    highest = start_highest
    temp_stop = current_stop
    for _, candle in df.iterrows():
        high, low, close_, open_ = candle['High'], candle['Low'], candle['Close'], candle['Open']
        new_tp = None
        if direction == "LONG":
            for i in range(len(tps)-1, -1, -1):
                if high >= tps[i] and i > highest:
                    new_tp = i
                    break
        else:
            for i in range(len(tps)-1, -1, -1):
                if low <= tps[i] and i > highest:
                    new_tp = i
                    break

        sl_hit = (low <= temp_stop) if direction == "LONG" else (high >= temp_stop)

        if new_tp is not None and sl_hit:
            if depth < 2:
                sub_out, sub_exit = resolve_ambiguous(
                    sym, direction, entry, temp_stop, tps, highest,
                    candle.name, candle.name + pd.Timedelta(hours=1) if interval=='15m' else candle.name + pd.Timedelta(minutes=15),
                    depth+1
                )
                return sub_out, sub_exit
            else:
                return resolve_heuristic_decision(direction, close_ > open_, new_tp, temp_stop, tps, highest)

        if new_tp is not None and not sl_hit:
            highest = new_tp
            temp_stop = update_stop(direction, highest, entry, tps)
            if direction == "LONG" and low <= temp_stop:
                return f"TP{highest+1}", temp_stop
            if direction == "SHORT" and high >= temp_stop:
                return f"TP{highest+1}", temp_stop
            continue

        if sl_hit and new_tp is None:
            return (f"TP{highest+1}" if highest >= 0 else "STOP LOSS"), temp_stop

    return None, None

def resolve_heuristic(sym, direction, entry, current_stop, tps, start_highest, start_time, end_time):
    df = binance_klines(sym, '1h', start_time=start_time, end_time=end_time)
    if df.empty:
        return "STOP LOSS", current_stop
    candle = df.iloc[0]
    high, low, open_, close_ = candle['High'], candle['Low'], candle['Open'], candle['Close']
    new_tp = None
    if direction == "LONG":
        for i in range(len(tps)-1, -1, -1):
            if high >= tps[i] and i > start_highest:
                new_tp = i
                break
    else:
        for i in range(len(tps)-1, -1, -1):
            if low <= tps[i] and i > start_highest:
                new_tp = i
                break
    if new_tp is None:
        return "STOP LOSS", current_stop
    return resolve_heuristic_decision(direction, close_ > open_, new_tp, current_stop, tps, start_highest)

def resolve_heuristic_decision(direction, is_bullish, new_tp, current_stop, tps, start_highest):
    if direction == "LONG":
        if is_bullish:
            new_stop = update_stop(direction, new_tp, None, tps)
            return f"TP{new_tp+1}", new_stop
        else:
            return "STOP LOSS", current_stop
    else:
        if not is_bullish:
            new_stop = update_stop(direction, new_tp, None, tps)
            return f"TP{new_tp+1}", new_stop
        else:
            return "STOP LOSS", current_stop

def check_open_trades():
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return

    if open_df.empty:
        return

    if "highest_tp" not in open_df.columns:
        open_df["highest_tp"] = -1
    if "quantity" not in open_df.columns:
        open_df["quantity"] = 0.0

    results = []
    still_open = []
    alerts = []
    tp_alerts = []
    now = datetime.now()
    mults = [0.4, 0.8, 1.2, 1.6, 2.0]

    for idx, trade in open_df.iterrows():
        sym = trade["symbol"]
        direction = trade["action"]
        entry = float(trade["entry"])
        stop_orig = float(trade["stop"])
        qty = float(trade.get("quantity", 0))
        risk = abs(entry - stop_orig)

        tps = []
        for m in mults:
            if direction == "LONG":
                tps.append(entry + m * risk)
            else:
                tps.append(entry - m * risk)

        try:
            entry_time = datetime.strptime(trade["timestamp"], "%Y-%m-%d %H:%M:%S")
        except:
            still_open.append(trade)
            continue

        df_1h = binance_klines(sym, '1h', start_time=entry_time, end_time=now)
        if df_1h.empty:
            still_open.append(trade)
            continue

        current_stop = stop_orig
        highest_tp_idx = int(trade.get("highest_tp", -1))
        if highest_tp_idx >= 0:
            current_stop = update_stop(direction, highest_tp_idx, entry, tps)

        outcome = None
        exit_price = None
        new_high = highest_tp_idx
        atr_4h = None

        for candle_time, candle in df_1h.iterrows():
            high = candle['High']
            low = candle['Low']
            open_ = candle['Open']
            close_ = candle['Close']

            new_tp_idx = None
            if direction == "LONG":
                for i in range(len(tps)-1, -1, -1):
                    if high >= tps[i] and i > highest_tp_idx:
                        new_tp_idx = i
                        break
            else:
                for i in range(len(tps)-1, -1, -1):
                    if low <= tps[i] and i > highest_tp_idx:
                        new_tp_idx = i
                        break

            sl_touched = (low <= current_stop) if direction == "LONG" else (high >= current_stop)

            if new_tp_idx is not None and sl_touched:
                sub_outcome, sub_exit = resolve_ambiguous(
                    sym, direction, entry, current_stop, tps, highest_tp_idx,
                    candle_time, candle_time + timedelta(hours=1)
                )
                if sub_outcome:
                    outcome = sub_outcome
                    exit_price = sub_exit
                    break
                continue

            if new_tp_idx is not None and not sl_touched:
                highest_tp_idx = new_tp_idx
                new_high = highest_tp_idx
                current_stop = update_stop(direction, highest_tp_idx, entry, tps)

                if highest_tp_idx >= 1:
                    if atr_4h is None:
                        df_4h = binance_klines(sym, '4h', limit=100)
                        atr_4h = get_4h_atr(df_4h, entry)
                    if direction == "LONG":
                        trail_stop = high - 1.5 * atr_4h
                        current_stop = max(current_stop, trail_stop)
                    else:
                        trail_stop = low + 1.5 * atr_4h
                        current_stop = min(current_stop, trail_stop)

                if highest_tp_idx == 0:
                    stop_desc = "BE"
                else:
                    stop_desc = f"TP{highest_tp_idx} (dynamic trail)"
                tp_alerts.append(f"🚀 {sym.replace('USDT','')} {direction} TP{highest_tp_idx+1} hit — SL moved to {stop_desc}")

                if direction == "LONG" and low <= current_stop:
                    outcome = f"TP{highest_tp_idx+1}"
                    exit_price = current_stop
                    break
                if direction == "SHORT" and high >= current_stop:
                    outcome = f"TP{highest_tp_idx+1}"
                    exit_price = current_stop
                    break
                continue

            if sl_touched and new_tp_idx is None:
                outcome = f"TP{highest_tp_idx+1}" if highest_tp_idx >= 0 else "STOP LOSS"
                exit_price = current_stop
                break

        if outcome is None:
            trade["highest_tp"] = new_high
            still_open.append(trade)
            continue

        pnl_usdt = qty * (exit_price - entry) if direction == "LONG" else qty * (entry - exit_price)
        result = trade.to_dict()
        result["hit_level"] = outcome
        result["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
        result["exit_price"] = exit_price
        result["pnl_usdt"] = round(pnl_usdt, 4)
        results.append(result)

        portfolio = load_portfolio()
        portfolio['balance_usdt'] += pnl_usdt
        portfolio['realized_pnl'] += pnl_usdt
        portfolio['open_positions'] -= 1
        save_portfolio(portfolio)

        pnl_pct = (exit_price - entry) / entry * 100 if direction == "LONG" else (entry - exit_price) / entry * 100
        icon = "🔔" if "TP" in outcome else "🔴"
        alerts.append(f"{icon} {sym.replace('USDT','')} {direction} → {outcome} ({pnl_pct:+.2f}%)")

    if results:
        append_csv(TRADE_RESULTS_CSV, pd.DataFrame(results))
    if still_open:
        df_still_open = pd.DataFrame(still_open)
        if "highest_tp" not in df_still_open.columns:
            df_still_open["highest_tp"] = -1
        portfolio = load_portfolio()
        portfolio['open_positions'] = len(df_still_open)
        save_csv(OPEN_TRADES_CSV, df_still_open)
        save_portfolio(portfolio)
    else:
        portfolio = load_portfolio()
        portfolio['open_positions'] = 0
        save_csv(OPEN_TRADES_CSV, pd.DataFrame())
        save_portfolio(portfolio)

    if tp_alerts and TELEGRAM_TOKEN:
        send_telegram("\n".join(tp_alerts))
    if alerts and TELEGRAM_TOKEN:
        msg = "Trade updates:\n" + "\n".join(alerts)
        send_telegram(msg)

# ========== UNIVERSE BUILDER ==========
def build_universe():
    tickers = binance_24hr_tickers()
    if not tickers:
        return []
    filtered = [t for t in tickers if t["quoteVolume"] > 500_000
                and t["symbol"].endswith("USDT")
                and "USDC" not in t["symbol"]
                and "BUSD" not in t["symbol"]]
    top = filtered[:SETTINGS["universe_top_n"]]
    return [t["symbol"] for t in top]

# ========== SIGNAL GENERATION ==========
def generate_signal(balance_usdt, backtest_mode=False):
    universe = build_universe()
    if not universe:
        return {"action": "HOLD", "reasoning": "No Binance universe available."}

    open_symbols = set()
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
        if not open_df.empty:
            open_symbols = set(open_df["symbol"].values)
    except:
        pass

    candidates = []
    for sym in universe:
        if sym in open_symbols:
            continue
        tickers = binance_24hr_tickers()
        ticker = next((t for t in tickers if t["symbol"] == sym), None)
        if ticker is None or float(ticker["lastPrice"]) <= 0:
            continue
        price = float(ticker["lastPrice"])
        volume = float(ticker["quoteVolume"])
        candidates.append({"symbol": sym, "price": price, "volume": volume})
    candidates.sort(key=lambda x: x["volume"], reverse=True)
    candidates = candidates[:50]
    if not candidates:
        return {"action": "HOLD", "reasoning": "No liquid candidates."}

    btc_4h = binance_klines("BTCUSDT", "4h", limit=200)
    btc_score = btc_trend_score(btc_4h)
    macro_score = institutional_macro_filter(btc_4h)

    best = None
    best_score = 0
    all_scored = []
    for coin in candidates:
        sym = coin["symbol"]
        price = coin["price"]
        df_4h = binance_klines(sym, "4h", limit=200)
        if df_4h.empty:
            continue
        total_score, layers, ema_dist, adx_val, trend_dir, errors = score_coin(sym, price, df_4h, btc_4h, macro_score)
        atr = get_4h_atr(df_4h, price)
        if atr / price * 100 > SETTINGS["volatility_cap_pct"]:
            total_score = 0.0
            errors.append("volatility cap")
        coin["score"] = total_score
        coin["atr"] = atr
        coin["layers"] = layers
        coin["ema_distance"] = ema_dist
        coin["adx_value"] = adx_val
        coin["trend_dir"] = trend_dir
        coin["errors"] = errors
        all_scored.append(coin)

        if best is None or abs(total_score) > abs(best_score):
            best = coin
            best_score = total_score

    if best is None or abs(best_score) < SETTINGS["conviction_threshold"]:
        best_sym = best["symbol"] if best else "none"
        reason = f"No strong conviction (best {best_sym} score {best_score:+.2f})"
        return {"action": "HOLD", "reasoning": reason}

    direction = "LONG" if best_score >= 0 else "SHORT"
    if (direction == "LONG" and best["trend_dir"] == "down") or \
       (direction == "SHORT" and best["trend_dir"] == "up"):
        return {"action": "HOLD", "reasoning": f"Signal {direction} rejected by 4h trend filter."}

    best_score += trend_strength_bonus(best["adx_value"], best_score)
    momentum_bonus = momentum_alignment_score(
        binance_klines(best["symbol"], "4h", limit=2), direction, best["layers"]
    )
    best_score += momentum_bonus

    if abs(best_score) < SETTINGS["conviction_threshold"]:
        return {"action": "HOLD", "reasoning": f"Final conviction {best_score:+.2f} below threshold."}

    entry = best["price"] * (0.999 if direction == "LONG" else 1.001)
    atr = best["atr"]
    min_stop_distance = max(SETTINGS["atr_stop_multiplier"] * atr, entry * 0.01)
    stop = entry - min_stop_distance if direction == "LONG" else entry + min_stop_distance
    risk_per_share = abs(entry - stop)

    step_size, min_qty, min_notional = binance_exchange_info(best["symbol"])
    risk_amount = balance_usdt * SETTINGS["risk_per_trade"]
    qty = risk_amount / risk_per_share
    qty = (qty // step_size) * step_size
    if qty < min_qty or qty * entry < min_notional or qty * entry < SETTINGS["min_notional"]:
        return {"action": "HOLD", "reasoning": f"Position size {qty:.6f} too small."}

    mults = [0.4, 0.8, 1.2, 1.6, 2.0]
    tps = []
    for m in mults:
        if direction == "LONG":
            tps.append(round(entry + m * risk_per_share, 6))
        else:
            tps.append(round(entry - m * risk_per_share, 6))

    conf, reason = call_groq_reasoning(best["symbol"], entry, atr, best["layers"], best["errors"])
    if conf < SETTINGS["ai_confidence_min"]:
        return {"action": "HOLD", "reasoning": f"AI confidence too low ({conf}/10). {reason}"}

    return {
        "action": direction,
        "symbol": best["symbol"],
        "quantity": round(qty, 6),
        "limit_price": round(entry, 6),
        "stop_loss": round(stop, 6),
        "take_profits": tps,
        "confidence_score": conf,
        "reasoning": reason,
        "conviction_score": round(best_score, 2),
    }

# ========== BACKTEST MODULE ==========
def backtest():
    print("Starting backtest...")
    initialize_trade_files()
    portfolio = load_portfolio()
    portfolio["balance_usdt"] = SETTINGS["backtest_initial_balance"]
    save_portfolio(portfolio)

    start = datetime.strptime(SETTINGS["backtest_start"], "%Y-%m-%d")
    end = datetime.strptime(SETTINGS["backtest_end"], "%Y-%m-%d")
    current = start
    while current <= end:
        print(f"Backtest: {current.strftime('%Y-%m-%d')}")
        signal = generate_signal(portfolio["balance_usdt"], backtest_mode=True)
        if signal["action"] in ["LONG", "SHORT"]:
            log_signal(signal)
            add_open_trade(signal)
            portfolio = load_portfolio()
            portfolio["open_positions"] += 1
            save_portfolio(portfolio)
        current += timedelta(hours=4)
    print("Backtest finished (simplified).")

# ========== TELEGRAM ==========
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("Telegram send failed:", e)

def get_daily_pnl():
    try:
        df = pd.read_csv(TRADE_RESULTS_CSV)
        today = datetime.now().strftime("%Y-%m-%d")
        df['close_time'] = pd.to_datetime(df['close_time'])
        daily = df[df['close_time'].dt.strftime("%Y-%m-%d") == today]
        return daily['pnl_usdt'].sum() if not daily.empty else 0.0
    except:
        return 0.0

# ========== MAIN ==========
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", action="store_true")
    args = parser.parse_args()

    if args.backtest:
        backtest()
        return

    initialize_trade_files()
    check_open_trades()

    portfolio = load_portfolio()
    balance = portfolio['balance_usdt']

    # 5% daily loss limit based on current balance
    daily_loss_limit_usd = -balance * (SETTINGS["daily_loss_limit_pct"] / 100)

    daily_pnl = get_daily_pnl()
    if daily_pnl <= daily_loss_limit_usd:
        msg = (f"Daily loss limit of {abs(daily_loss_limit_usd):.2f} USD reached "
               f"(PnL: {daily_pnl:.2f}). No new trades.")
        if TELEGRAM_TOKEN: send_telegram(msg)
        return

    if portfolio['open_positions'] >= SETTINGS["max_positions"]:
        msg = f"Max positions reached ({portfolio['open_positions']}/{SETTINGS['max_positions']})."
        if TELEGRAM_TOKEN: send_telegram(msg)
        return

    signal = generate_signal(balance)
    if signal["action"] in ["LONG", "SHORT"]:
        log_signal(signal)
        add_open_trade(signal)
        portfolio['open_positions'] += 1
        save_portfolio(portfolio)

        sym = signal['symbol'].replace("USDT", "")
        icon = "🟢" if signal["action"] == "LONG" else "🔴"
        tp_str = " / ".join([f"{tp:,.6f}" for tp in signal['take_profits']])
        conviction_str = f"{signal['conviction_score']:+.2f}"
        msg = (
            f"{icon} ${sym} {signal['action']}\n"
            f"Entry: {signal['limit_price']:,.6f}\n"
            f"Stop: {signal['stop_loss']:,.6f}\n"
            f"Targets: {tp_str}\n"
            f"Confidence: {conviction_str} (AI {signal['confidence_score']}/10)\n"
            f"Reason: {signal['reasoning']}"
        )
        if TELEGRAM_TOKEN: send_telegram(msg)
    else:
        msg = f"HOLD\n{signal.get('reasoning', 'No signal')}"
        if TELEGRAM_TOKEN: send_telegram(msg)

if __name__ == "__main__":
    main()