import requests, json, os, traceback, re, time
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

# ========== ENVIRONMENT ==========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not set in secrets.")

# ========== PERSISTENT PORTFOLIO ==========
PORTFOLIO_FILE = "portfolio.json"

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE) as f:
                data = json.load(f)
            return {
                "balance_usdt": data.get("balance_usdt", 1000.0),
                "realized_pnl": data.get("realized_pnl", 0.0),
                "open_positions": data.get("open_positions", 0),
                "daily_loss_limit": data.get("daily_loss_limit", -20)
            }
        except:
            pass
    return {
        "balance_usdt": 1000.0,
        "realized_pnl": 0.0,
        "open_positions": 0,
        "daily_loss_limit": -20
    }

def save_portfolio(p):
    try:
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(p, f, indent=2)
    except:
        print("Warning: Could not save portfolio.json")

portfolio = load_portfolio()

# ========== CSV FILE PATHS ==========
TRADE_LOG_CSV = "trade_log.csv"
OPEN_TRADES_CSV = "open_trades.csv"
TRADE_RESULTS_CSV = "trade_results.csv"

# ========== DATA HELPERS ==========
def fetch_coingecko(url, retries=2):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(2 ** attempt)
        except:
            time.sleep(1)
    return None

def get_yahoo_klines(symbol_usdt, interval='4h', days=60, start=None, end=None):
    yahoo_symbol = symbol_usdt.replace("USDT", "-USD")
    if start is None:
        end = datetime.now()
        start = end - timedelta(days=days)
    else:
        if end is None:
            end = datetime.now()
    try:
        df = yf.download(yahoo_symbol, start=start, end=end, interval=interval, progress=False)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except:
        return pd.DataFrame()

# ========== CSV LOGGING ==========
def init_csv(filepath, columns):
    if not os.path.exists(filepath):
        df = pd.DataFrame(columns=columns)
        df.to_csv(filepath, index=False)

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
                               "TP1", "TP2", "TP3", "TP4", "TP5", "status",
                               "quantity", "original_qty", "highest_tp"])
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
    df = pd.DataFrame([row])
    append_csv(TRADE_LOG_CSV, df)

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
        "original_qty": signal["quantity"],
        "highest_tp": -1
    }
    df = pd.DataFrame([row])
    append_csv(OPEN_TRADES_CSV, df)

# ========== PORTFOLIO HELPERS ==========
def get_daily_pnl():
    try:
        df = pd.read_csv(TRADE_RESULTS_CSV)
        if df.empty:
            return 0.0
        today = datetime.now().strftime("%Y-%m-%d")
        df['close_time'] = pd.to_datetime(df['close_time'])
        daily = df[df['close_time'].dt.strftime("%Y-%m-%d") == today]
        if daily.empty:
            return 0.0
        return daily['pnl_usdt'].sum()
    except:
        return 0.0

def update_portfolio(trade_result):
    portfolio['balance_usdt'] += trade_result['pnl_usdt']
    portfolio['realized_pnl'] += trade_result['pnl_usdt']
    save_portfolio(portfolio)

# ========== INSTITUTIONAL IMPROVEMENTS ==========

def institutional_macro_filter():
    df_btc = get_yahoo_klines("BTCUSDT", interval='4h', days=14)
    if df_btc.empty or len(df_btc) < 50:
        return 0
    closes = df_btc['Close']
    ema50 = closes.ewm(span=50, adjust=False).mean()
    current = closes.iloc[-1]
    btc_bullish = current > ema50.iloc[-1]

    try:
        data = fetch_coingecko("https://api.coingecko.com/api/v3/global")
        if data and 'data' in data:
            dom = data['data'].get('market_cap_percentage', {}).get('usdt', 0)
            usdt_dom = dom
        else:
            usdt_dom = 5
    except:
        usdt_dom = 5

    macro_score = 0
    if btc_bullish:
        macro_score += 1
        if usdt_dom < 4.5:
            macro_score += 1
    else:
        macro_score -= 1
        if usdt_dom > 6.5:
            macro_score -= 1

    return max(-2, min(2, macro_score))

def anchored_vwap_score(df, current_price):
    if len(df) < 50:
        return 0
    df = df.copy()
    typical = (df['High'] + df['Low'] + df['Close']) / 3
    df['vpv'] = typical * df['Volume']
    total_vol = df['Volume'].sum()
    if total_vol == 0:
        return 0
    vwap = df['vpv'].sum() / total_vol
    deviation = (current_price - vwap) / vwap * 100
    if deviation > 1:
        return 1
    elif deviation < -1:
        return -1
    else:
        return 0

def refined_buying_pressure(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=10)
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

# ========== TRAILING STOP LOGIC (partial exits) ==========

def check_open_trades():
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return

    if open_df.empty:
        return

    if "timestamp" in open_df.columns:
        open_df = open_df.sort_values("timestamp").drop_duplicates(subset="symbol", keep="last")
    else:
        open_df = open_df.drop_duplicates(subset="symbol", keep="last")

    for col in ["highest_tp", "quantity", "original_qty"]:
        if col not in open_df.columns:
            open_df[col] = 0.0 if col != "highest_tp" else -1

    results = []
    still_open = []
    alerts = []
    now = datetime.now()
    mults = [0.4, 0.8, 1.2, 1.6, 2.0]
    fractions = [0.20, 0.20, 0.30]

    for idx, trade in open_df.iterrows():
        sym = trade["symbol"]
        direction = trade["action"]
        entry = float(trade["entry"])
        stop_orig = float(trade["stop"])
        original_qty = float(trade.get("original_qty", trade.get("quantity", 0)))
        remaining_qty = float(trade.get("quantity", original_qty))
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

        df_1h = get_yahoo_klines(sym, interval='1h', start=entry_time, end=now)
        if df_1h.empty:
            still_open.append(trade)
            continue

        highest_tp_idx = int(trade.get("highest_tp", -1))
        current_stop = entry if highest_tp_idx >= 0 else stop_orig

        for candle_time, candle in df_1h.iterrows():
            high = candle['High']
            low = candle['Low']

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

            if new_tp_idx is not None:
                for i in range(highest_tp_idx+1, new_tp_idx+1):
                    if remaining_qty <= 0:
                        break

                    if i <= 2:
                        fraction = fractions[i]
                        exit_qty = original_qty * fraction
                        if exit_qty > remaining_qty:
                            exit_qty = remaining_qty
                        if exit_qty > 0:
                            exit_price = tps[i]
                            pnl = (exit_price - entry) * exit_qty if direction == "LONG" else (entry - exit_price) * exit_qty
                            partial = trade.to_dict()
                            partial["hit_level"] = f"TP{i+1} (partial)"
                            partial["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                            partial["exit_price"] = exit_price
                            partial["quantity"] = exit_qty
                            partial["pnl_usdt"] = round(pnl, 4)
                            results.append(partial)
                            update_portfolio({'pnl_usdt': pnl})
                            remaining_qty -= exit_qty
                            highest_tp_idx = i
                            if i == 0:
                                current_stop = entry
                        alerts.append(f"🚀 {sym.replace('USDT','')} {direction} TP{i+1} hit — {fraction*100:.0f}% closed, SL now {'BE' if i==0 else 'at entry'}")

                    elif i == 4:
                        if remaining_qty > 0:
                            exit_price = tps[4]
                            pnl = (exit_price - entry) * remaining_qty if direction == "LONG" else (entry - exit_price) * remaining_qty
                            final = trade.to_dict()
                            final["hit_level"] = "TP5 (final)"
                            final["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                            final["exit_price"] = exit_price
                            final["quantity"] = remaining_qty
                            final["pnl_usdt"] = round(pnl, 4)
                            results.append(final)
                            update_portfolio({'pnl_usdt': pnl})
                            remaining_qty = 0
                            highest_tp_idx = 4
                            alerts.append(f"🔔 {sym.replace('USDT','')} {direction} TP5 hit — remaining closed")
                        break
                    else:
                        highest_tp_idx = 3

                if remaining_qty <= 0:
                    break

            if remaining_qty > 0:
                sl_hit = (low <= current_stop) if direction == "LONG" else (high >= current_stop)
                if sl_hit:
                    exit_price = current_stop
                    pnl = (exit_price - entry) * remaining_qty if direction == "LONG" else (entry - exit_price) * remaining_qty
                    final = trade.to_dict()
                    desc = "STOP LOSS" if highest_tp_idx == -1 else f"STOP LOSS (after TP{highest_tp_idx+1})"
                    final["hit_level"] = desc
                    final["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                    final["exit_price"] = exit_price
                    final["quantity"] = remaining_qty
                    final["pnl_usdt"] = round(pnl, 4)
                    results.append(final)
                    update_portfolio({'pnl_usdt': pnl})
                    remaining_qty = 0
                    alerts.append(f"🔴 {sym.replace('USDT','')} {direction} → {desc} (remaining closed)")
                    break

        if remaining_qty > 0:
            trade["highest_tp"] = highest_tp_idx
            trade["quantity"] = remaining_qty
            still_open.append(trade)

    if results:
        df_results = pd.DataFrame(results)
        append_csv(TRADE_RESULTS_CSV, df_results)

    if still_open:
        df_still = pd.DataFrame(still_open)
        for col in ["original_qty", "quantity", "highest_tp"]:
            if col not in df_still.columns:
                df_still[col] = 0 if col != "highest_tp" else -1
        portfolio['open_positions'] = len(df_still)
        save_csv(OPEN_TRADES_CSV, df_still)
    else:
        portfolio['open_positions'] = 0
        save_csv(OPEN_TRADES_CSV, pd.DataFrame())
    save_portfolio(portfolio)

    if alerts:
        msg = "Trade updates:\n" + "\n".join(alerts)
        send_telegram(msg)

# ========== 4‑HOUR ANALYSIS ==========
def get_technicals(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=14)
    error = None
    if df.empty or len(df) < 50:
        error = f"insufficient 4h data ({len(df)} candles)"
        return {
            "trend": 0, "adx": 0, "structure": 0,
            "combined": 0, "ema50_distance": 1.0, "error": error
        }
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

    adx_series, di_plus, di_minus = calc_adx(highs, lows, closes, 14)
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
    recent_lows  = lows.iloc[-lookback:]
    swing_highs = []
    swing_lows  = []
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
            if len(swing_highs) >= 3 and len(swing_lows) >= 3:
                prev_hh = swing_highs[-2][1] > swing_highs[-3][1]
                prev_hl = swing_lows[-2][1] > swing_lows[-3][1]
                if prev_hh and prev_hl:
                    structure_score = 3.0
                else:
                    structure_score = 2.0
            else:
                structure_score = 2.0
        elif (not last_hh) and (not last_hl):
            if len(swing_highs) >= 3 and len(swing_lows) >= 3:
                prev_lh = swing_highs[-2][1] < swing_highs[-3][1]
                prev_ll = swing_lows[-2][1] < swing_lows[-3][1]
                if prev_lh and prev_ll:
                    structure_score = -3.0
                else:
                    structure_score = -2.0
            else:
                structure_score = -2.0
    structure_score = max(-3, min(3, structure_score))

    combined = (
        trend * 0.30 +
        adx_score * 0.25 +
        structure_score * 0.45
    )
    ema50_val = ema50.iloc[-1]
    distance_pct = abs(current - ema50_val) / current
    trend_dir = "up" if current > ema50.iloc[-1] else "down"
    return {
        "trend": trend, "adx": adx_score, "structure": structure_score,
        "combined": combined, "ema50_distance": distance_pct,
        "adx_value": adx_now, "trend_dir": trend_dir, "error": None
    }

def get_4h_atr(symbol_usdt, current_price):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=14)
    if df.empty or len(df) < 14:
        return current_price * 0.02, "ATR data insufficient, using 2% fallback"
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    if pd.isna(atr):
        return current_price * 0.02, "ATR calculation failed, using 2% fallback"
    return atr, None

def get_buying_pressure(symbol_usdt):
    short_p, long_p = refined_buying_pressure(symbol_usdt)
    if short_p * long_p > 0:
        score = (short_p + long_p) / 2 * 3
    else:
        score = (short_p + long_p) / 2 * 3 * 0.3
    return score, None

def get_volatility_score(symbol_usdt, current_price):
    atr, atr_err = get_4h_atr(symbol_usdt, current_price)
    atr_pct = atr / current_price * 100
    if atr_pct < 2 or atr_pct > 7:
        return -1, atr_err
    return 1, None

def btc_trend_score():
    df = get_yahoo_klines("BTCUSDT", interval='4h', days=14)
    if df.empty or len(df) < 50:
        return 0, "BTC data unavailable"
    closes = df['Close']
    ema50 = closes.ewm(span=50, adjust=False).mean()
    current = closes.iloc[-1]
    ema_now = ema50.iloc[-1]
    if len(ema50) >= 7:
        ema_prev = ema50.iloc[-7]
        slope_up = ema_now > ema_prev
    else:
        slope_up = True
    price_above = current > ema_now
    if price_above and slope_up:
        return 2, None
    elif not price_above and not slope_up:
        return -2, None
    else:
        return 0, None

def volume_trend_score(symbol_usdt, direction=None):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=5)
    if df.empty or len(df) < 12:
        return 0, f"volume data insufficient ({len(df)} candles)"
    recent = df['Volume'].tail(6)
    first_half = recent[:3].mean()
    second_half = recent[3:].mean()
    if second_half > first_half * 1.05:
        if direction == "down":
            return -2, None
        return 2, None
    elif second_half < first_half * 0.95:
        if direction == "up":
            return -2, None
        return -2, None
    return 0, None

def momentum_alignment_score(symbol_usdt, direction, layers):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=2)
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

def trend_strength_bonus(adx_value, base_score):
    if adx_value > 35 and abs(base_score) > 0.5:
        return 0.30 * (1 if base_score > 0 else -1)
    elif adx_value > 30 and abs(base_score) > 0.5:
        return 0.20 * (1 if base_score > 0 else -1)
    return 0.0

# ========== SCORING ENGINE ==========
def score_coin(symbol, price, volume_24h, change1h, btc_score, btc_error, macro_score):
    errors = []
    tech = get_technicals(symbol)
    if tech.get("error"):
        errors.append(f"tech({symbol}): {tech['error']}")
    tech_combined = tech["combined"]
    ema50_distance = tech["ema50_distance"]
    adx_value = tech.get("adx_value", 0)
    trend_dir = tech.get("trend_dir", "up")

    buying_score, buy_err = get_buying_pressure(symbol)
    if buy_err:
        errors.append(f"buying_press({symbol}): {buy_err}")

    vol_score, vol_err = get_volatility_score(symbol, price)
    if vol_err:
        errors.append(f"volatility({symbol}): {vol_err}")

    intermarket_s = btc_score
    if btc_error:
        errors.append(f"intermarket: {btc_error}")

    vol_trend_s, vt_err = volume_trend_score(symbol, direction=trend_dir)
    if vt_err:
        errors.append(f"volume_trend({symbol}): {vt_err}")

    df_vwap = get_yahoo_klines(symbol, interval='4h', days=14)
    vwap_score = anchored_vwap_score(df_vwap, price)

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

def compute_confidence(layers):
    scores = [layers["tech"], layers["buying_press"], layers["intermarket"], layers["volume_trend"]]
    bearish = sum(1 for s in scores if s < -0.5)
    bullish = sum(1 for s in scores if s > 0.5)
    aligned = max(bearish, bullish)
    if aligned >= 4: return 7
    if aligned >= 3: return 6
    if aligned >= 2: return 5
    return 4

# ========== QWEN DEEP EVALUATOR (ANTI‑TEMPLATE, DYNAMIC) ==========
def evaluate_deep(coin, direction, btc_score, macro_score):
    sym = coin["symbol"]
    ticker = sym.replace("USDT", "")
    price = coin["price"]
    atr = coin["atr"]
    layers = coin["layers"]
    tech = get_technicals(sym)
    trend_dir = tech.get("trend_dir", "up")
    ema_rel = "above" if trend_dir == "up" else "below"
    # more detailed EMA slope
    ema_slope = "rising" if trend_dir == "up" else "falling"
    vwap_score = anchored_vwap_score(get_yahoo_klines(sym, interval='4h', days=14), price)
    vwap_rel = "above" if vwap_score > 0 else "below" if vwap_score < 0 else "near"
    # volume trend description
    vol_trend = "increasing" if layers["volume_trend"] > 0 else "decreasing" if layers["volume_trend"] < 0 else "flat"
    # BTC trend text
    btc_bullish = btc_score > 0
    btc_text = "bullish" if btc_bullish else "bearish"

    # Build a varied, anti‑template prompt
    prompt = (
        f"You are a professional Crypto Analyst writing a unique Binance Square post for ${ticker} (USDT pair). "
        f"The current analysis shows a {direction} setup on the 4‑hour chart. "
        f"Here are the SPECIFIC technical details for THIS COIN ONLY – use them to write a fresh, original post:\n\n"
        f"• Price: {price:.4f}\n"
        f"• EMA50: {ema_rel} price, currently {ema_slope}\n"
        f"• Anchored VWAP: price is {vwap_rel} it\n"
        f"• Volume trend: {vol_trend}\n"
        f"• $BTC trend: {btc_text}\n"
        f"• Internal conviction: {abs(coin['score']):.2f}\n\n"
        "WRITING RULES (MUST FOLLOW):\n"
        "1. Do NOT use the phrase 'order books are thinning out' or any generic template.\n"
        "2. Vary the hook and the reasoning for every post. Never repeat the same structure.\n"
        "3. Describe the specific EMA/VWAP/volume behaviour for this coin only.\n"
        "4. Use short paragraphs (1-2 sentences) for mobile readability.\n\n"
        "STRUCTURE THE POST IN THESE FOUR SECTIONS:\n\n"
        "1. THE COMPLIANT HOOK 🪝\n"
        "A single scroll‑stopping line with $TICKER. Be creative, never reuse previous openings.\n\n"
        "2. THE HUMANISED CHART READING 📈\n"
        "Explain the setup like a trader would, referencing the EMA50 slope, VWAP location, volume dynamics, and BTC context.\n\n"
        "3. RISK-MANAGED LEVELS 🎯\n"
        "Exactly as shown (use the real values I will provide separately, but I'll insert them later).\n\n"
        "4. THE ALGO-BOOSTER (CTA) & SAFE FOOTER 💬\n"
        "An open‑ended question encouraging engagement, plus hashtags and disclaimer.\n\n"
        "IMPORTANT: Output ONLY the final post text. No extra commentary, no RATING prefix."
    )

    def call_qwen(prompt, temp=0.9):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "qwen-2.5-72b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temp,
            "max_tokens": 500
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
        except:
            pass
        return None

    text = call_qwen(prompt, temp=0.9)
    # Retry if too short or if it repeats generic phrasing
    if not text or len(text) < 200 or "order books are thinning out" in text.lower():
        text = call_qwen(prompt, temp=1.0)

    # Fallback: build a dynamic, non‑repetitive post manually
    if not text:
        # Build unique description based on actual values
        if ema_slope == "rising" and vwap_rel == "above":
            chart_desc = f"The 50‑EMA is sloping upward with price holding comfortably above it, while the anchored VWAP from the last two weeks is providing dynamic support. {vol_trend.capitalize()} volume adds confidence to the move."
        elif ema_slope == "falling" and vwap_rel == "below":
            chart_desc = f"The 50‑EMA is trending lower and price is struggling below it, with the anchored VWAP acting as overhead resistance. {vol_trend.capitalize()} volume reinforces the bearish pressure."
        else:
            chart_desc = f"The 50‑EMA is {ema_slope} while price sits {ema_rel} it, and the anchored VWAP is {vwap_rel} the current price, acting as a pivot. Volume is {vol_trend}, suggesting market indecision."
        btc_note = f"$BTC is currently in a {btc_text} structure, which {'supports' if btc_bullish else 'weighs on'} altcoin setups like ${ticker}."

        sl_pct = abs(price - coin.get("stop_loss", price*0.98)) / price * 100
        tps = []
        risk_per_share = abs(price - coin.get("stop_loss", price*0.98))
        for m in [0.4, 0.8, 1.2, 1.6, 2.0]:
            if direction == "LONG":
                tps.append(round(price + m * risk_per_share, 6))
            else:
                tps.append(round(price - m * risk_per_share, 6))
        tp_str = " / ".join([f"{tp:.6f}" for tp in tps])

        hooks = [
            f"The compression on ${ticker} is becoming impossible to ignore. ⚡",
            f"${ticker} is coiling up in a tight range – a breakout is brewing.",
            f"Volume is starting to stir on ${ticker}. Here’s what the 4H chart is signalling.",
            f"The EMA50 on ${ticker} just gave a critical cue. Don’t overlook it.",
        ]
        import random
        hook = random.choice(hooks)

        text = (
            f"{hook}\n\n"
            f"{chart_desc}\n\n"
            f"{btc_note}\n\n"
            f"🟢 {direction} Setup Structure:\n"
            f"• Area of Interest: {price:.6f}\n"
            f"• Technical Invalidation: {coin.get('stop_loss', price*0.98):.6f} ({sl_pct:.2f}%)\n"
            f"• Target Objectives: {tp_str}\n\n"
            f"What’s your read on ${ticker} – are you waiting for a retest or already positioned? Let me know below!\n"
            f"#CryptoAnalysis #{ticker} #TechnicalAnalysis #BinanceSquare\n"
            f"*Disclaimer: This analysis is based on technical indicators for educational and informational purposes only. This is not financial advice. Always practice strict risk management and do your own research (DYOR).*"
        )

    # Qwen occasionally still prefixes with "RATING: X |" – strip it
    text = re.sub(r'^RATING:\s*\d+\s*\|?\s*', '', text).strip()

    # Rating extraction for internal scoring
    rating = 5
    rat_match = re.search(r'RATING:\s*(\d+)', text)
    if rat_match:
        rating = int(rat_match.group(1))
    rating = max(1, min(10, rating))

    return rating, text  # the full post

# ========== SIGNAL GENERATION (unchanged except post_text handling) ==========
def generate_signal(balance_usdt):
    cg_url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1"
    coins_data = fetch_coingecko(cg_url)
    if not coins_data:
        return {"action": "HOLD", "reasoning": "CoinGecko market data unavailable."}

    open_symbols = set()
    risky_count = 0
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
        if not open_df.empty:
            if "timestamp" in open_df.columns:
                open_df = open_df.sort_values("timestamp").drop_duplicates(subset="symbol", keep="last")
            else:
                open_df = open_df.drop_duplicates(subset="symbol", keep="last")
            open_symbols = set(open_df["symbol"].values)
            if "highest_tp" in open_df.columns:
                risky_count = (open_df["highest_tp"] == -1).sum()
            else:
                risky_count = len(open_df)
    except:
        pass

    if risky_count >= 3:
        return {"action": "HOLD", "reasoning": f"Max 3 risky trades ({risky_count}). Waiting for TP1."}

    candidates = []
    for coin in coins_data:
        sym = coin.get("symbol", "").upper() + "USDT"
        if coin.get("current_price", 0) > 0 and sym not in open_symbols:
            candidates.append({"symbol": sym, "price": coin["current_price"], "volume": coin.get("total_volume", 0)})
    candidates.sort(key=lambda x: x["volume"], reverse=True)
    candidates = candidates[:50]

    if not candidates:
        return {"action": "HOLD", "reasoning": "No liquid coins available."}

    btc_score, btc_error = btc_trend_score()
    macro_score = institutional_macro_filter()

    all_scored = []
    for coin in candidates:
        total, layers, ema_dist, adx, trend_dir, errors = score_coin(
            coin["symbol"], coin["price"], coin["volume"], 0, btc_score, btc_error, macro_score
        )
        atr, _ = get_4h_atr(coin["symbol"], coin["price"])
        if atr / coin["price"] > 0.07:
            total = 0.0
            errors.append("volatility cap (>7%)")
        coin["score"] = total
        coin["atr"] = atr
        coin["layers"] = layers
        coin["adx"] = adx
        coin["trend_dir"] = trend_dir
        coin["errors"] = errors
        all_scored.append(coin)

    top_candidates = sorted(all_scored, key=lambda x: abs(x["score"]), reverse=True)[:5]

    best_combined = -999
    best_signal = None

    for coin in top_candidates:
        if abs(coin["score"]) < 0.5:
            continue
        direction = "LONG" if coin["score"] >= 0 else "SHORT"
        rating, post_text = evaluate_deep(coin, direction, btc_score, macro_score)
        if rating < 4:
            continue
        combined = abs(coin["score"]) * (rating / 5.0)
        if combined > best_combined:
            best_combined = combined
            coin["direction"] = direction
            coin["rating"] = rating
            coin["post_text"] = post_text
            coin["conviction_score"] = round(combined, 2)
            coin["conviction10_str"] = (f"+{round(combined * 10 / 3)}/10" if combined >= 0 else f"{round(combined * 10 / 3)}/10")
            best_signal = coin

    if best_signal is None:
        best = max(all_scored, key=lambda x: abs(x["score"]))
        if abs(best["score"]) < 1.49:
            return {"action": "HOLD", "reasoning": f"No strong conviction. Best internal: {best['score']:.2f}"}
        direction = "LONG" if best["score"] >= 0 else "SHORT"
        best["direction"] = direction
        # dynamic fallback
        ticker = best["symbol"].replace("USDT", "")
        price = best["price"]
        sl_pct = abs(price - best.get("stop_loss", price*0.98)) / price * 100
        tps = []
        risk_per_share = abs(price - best.get("stop_loss", price*0.98))
        for m in [0.4, 0.8, 1.2, 1.6, 2.0]:
            if direction == "LONG":
                tps.append(round(price + m * risk_per_share, 6))
            else:
                tps.append(round(price - m * risk_per_share, 6))
        tp_str = " / ".join([f"{tp:.6f}" for tp in tps])
        best["post_text"] = (
            f"The compression on ${ticker} is becoming impossible to ignore. ⚡\n\n"
            f"Price is hovering near a pivotal zone with the 50‑EMA and VWAP converging – a classic coil before a directional move. Volume is { 'rising' if best.get('layers', {}).get('volume_trend', 0) > 0 else 'fading' }, hinting at an imminent expansion.\n\n"
            f"$BTC is providing a { 'supportive' if btc_score > 0 else 'cautious' } macro backdrop.\n\n"
            f"🟢 {direction} Setup Structure:\n"
            f"• Area of Interest: {price:.6f}\n"
            f"• Technical Invalidation: {best.get('stop_loss', price*0.98):.6f} ({sl_pct:.2f}%)\n"
            f"• Target Objectives: {tp_str}\n\n"
            f"Are you waiting for a clean breakout on ${ticker}, or already scaling in? Share your view!\n"
            f"#CryptoAnalysis #{ticker} #TechnicalAnalysis #BinanceSquare\n"
            f"*Disclaimer: This analysis is based on technical indicators for educational and informational purposes only. This is not financial advice. Always practice strict risk management and do your own research (DYOR).*"
        )
        best["rating"] = 5
        best["conviction_score"] = abs(best["score"])
        best["conviction10_str"] = "0/10"
        best_signal = best

    entry_price = best_signal.get("bid", best_signal["price"] * 0.999) if best_signal["direction"] == "LONG" else best_signal.get("ask", best_signal["price"] * 1.001)
    atr = best_signal["atr"]
    min_stop = max(1.5 * atr, entry_price * 0.01)
    stop = entry_price - min_stop if best_signal["direction"] == "LONG" else entry_price + min_stop
    risk_per_share = abs(entry_price - stop)
    qty = round((balance_usdt * 0.01) / risk_per_share, 6)

    mults = [0.4, 0.8, 1.2, 1.6, 2.0]
    tps = []
    for m in mults:
        if best_signal["direction"] == "LONG":
            tps.append(round(entry_price + m * risk_per_share, 6))
        else:
            tps.append(round(entry_price - m * risk_per_share, 6))

    # Replace placeholder levels in post_text with exact calculated levels
    post_text = best_signal.get("post_text", "")
    sl_pct = abs(entry_price - stop) / entry_price * 100
    tp_str = " / ".join([f"{tp:.6f}" for tp in tps])
    post_text = re.sub(r'• Area of Interest: .*', f'• Area of Interest: {entry_price:.6f}', post_text)
    post_text = re.sub(r'• Technical Invalidation: .*', f'• Technical Invalidation: {stop:.6f} ({sl_pct:.2f}%)', post_text)
    post_text = re.sub(r'• Target Objectives: .*', f'• Target Objectives: {tp_str}', post_text)

    return {
        "action": best_signal["direction"],
        "symbol": best_signal["symbol"],
        "quantity": qty,
        "limit_price": entry_price,
        "stop_loss": stop,
        "take_profits": tps,
        "confidence_score": compute_confidence(best_signal["layers"]),
        "conviction_score": best_signal["conviction_score"],
        "post_text": post_text,
        "best_candidate": best_signal
    }

# ========== DARK CHART ==========
def send_trade_chart(signal, title_suffix=""):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import mplfinance as mpf

        sym = signal['symbol']
        df = get_yahoo_klines(sym, interval='4h', days=10)
        if df.empty or len(df) < 20:
            return

        mpf_style = mpf.make_mpf_style(
            base_mpf_style='nightclouds',
            facecolor='#000000',
            gridcolor='#2a2e39',
            rc={'axes.labelcolor': 'white',
                'xtick.color': 'white',
                'ytick.color': 'white',
                'axes.titlecolor': 'white'}
        )

        ema50 = df['Close'].ewm(span=50, adjust=False).mean()
        typical = (df['High'] + df['Low'] + df['Close']) / 3
        vwap = (typical * df['Volume']).cumsum() / df['Volume'].cumsum()

        apds = [
            mpf.make_addplot(ema50, color='#f39c12', width=1.5, label='EMA50'),
            mpf.make_addplot(vwap, color='#3498db', width=1, linestyle='--', label='VWAP')
        ]

        title = f"{sym.replace('USDT','')} 4h"
        if title_suffix:
            title += title_suffix

        fig, axes = mpf.plot(df, type='candle', style=mpf_style,
                             title=title, ylabel='Price', addplot=apds,
                             returnfig=True, figsize=(8,6))
        ax = axes[0]

        entry = signal.get('limit_price')
        stop = signal.get('stop_loss')
        tps = signal.get('take_profits')
        if entry is not None and stop is not None:
            ax.axhline(y=entry, color='#f1c40f', linestyle='--', linewidth=1.5, label='Entry')
            ax.axhline(y=stop, color='#e74c3c', linestyle='--', linewidth=1.5, label='Stop')
            if tps:
                for i, tp in enumerate(tps):
                    ax.axhline(y=tp, color='#2ecc71', linestyle='--', linewidth=1, alpha=0.8,
                               label=f'TP{i+1}' if i==0 else None)
            ax.legend(loc='upper left', facecolor='#000000', edgecolor='white', labelcolor='white')

        chart_path = f"{sym.replace('USDT','')}_chart.png"
        fig.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor='black')
        plt.close(fig)

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(chart_path, 'rb') as img:
            requests.post(url, data={'chat_id': CHAT_ID}, files={'photo': img})
        os.remove(chart_path)
    except ImportError:
        sym = signal['symbol']
        base = sym.replace("USDT", "").upper()
        studies = "&studies[]=STD%3BEMA%3B50&studies[]=STD%3BVWAP"
        url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{base}USDT&interval=240{studies}"
        send_telegram(f"📈 Chart with EMA & VWAP: {url}")
    except Exception as e:
        print(f"Chart error: {e}")
        sym = signal['symbol']
        base = sym.replace("USDT", "").upper()
        studies = "&studies[]=STD%3BEMA%3B50&studies[]=STD%3BVWAP"
        url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{base}USDT&interval=240{studies}"
        send_telegram(f"📈 Chart with EMA & VWAP: {url}")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("Telegram send failed:", e)

def main():
    try:
        initialize_trade_files()
        print("Checking open trades...")
        check_open_trades()

        daily_pnl = get_daily_pnl()
        if daily_pnl <= portfolio['daily_loss_limit']:
            msg = f"Daily loss limit reached (PnL: {daily_pnl:.2f} USD). No new trades today."
            send_telegram(msg)
            return

        balance = portfolio['balance_usdt']
        dec = generate_signal(balance)
        action = dec.get('action', 'HOLD')
        if action in ["LONG", "SHORT"]:
            log_signal(dec)
            add_open_trade(dec)
            portfolio['open_positions'] += 1
            save_portfolio(portfolio)

            # Send the ready-to-publish Binance Square post
            post_text = dec.get('post_text', '')
            if post_text:
                send_telegram(post_text)

            # Send chart separately
            send_trade_chart(dec)
        else:
            msg = f"HOLD\n{dec.get('reasoning', 'No signal')}"
            send_telegram(msg)
            best = dec.get('best_candidate')
            if best:
                send_trade_chart({
                    'symbol': best['symbol'],
                    'limit_price': None,
                    'stop_loss': None,
                    'take_profits': None
                })
    except Exception as e:
        err_msg = f"Bot crashed: {traceback.format_exc()}"
        print(err_msg)
        send_telegram(err_msg[:500])

if __name__ == "__main__":
    main()