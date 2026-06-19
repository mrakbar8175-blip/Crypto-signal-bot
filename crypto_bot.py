#!/usr/bin/env python3
"""
Crypto Swing Bot – Top 50 liquid coins via CoinGecko, 0.5R TP1 Optimized, 5 TPs (0.5/1/2/3/5R)
Wider stop loss (2.5x ATR, 1.0‑6.0% bounds) for swing tolerance
Signal formatting: Elite 7-angle Binance Square posts
Breakeven after TP1, allows new signals on same pair
BLACKLIST for unwanted coins (stablecoins, QUQ, etc.)
HOLD message shows conviction scores + layer breakdown (with failure reasons)
Enhanced alert visibility with console prints & error handling.
"""

import requests, json, os, traceback, random, math
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

# ========== ENVIRONMENT ==========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    print("WARNING: GROQ_API_KEY not set – AI filtering disabled.")

# ========== BLACKLIST – COINS WE NEVER TRADE ==========
BLACKLIST = {
    "QUQ",
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "FDUSD"
}

# ========== DYNAMIC COIN LIST (CoinGecko) ==========
def fetch_top_liquid_coins(limit=50):
    global COIN_RANK
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": limit,
        "page": 1,
        "sparkline": False,
        "price_change_percentage": "24h"
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        yf_symbols = []
        COIN_RANK = {}
        rank = 1
        for coin in data:
            symbol = coin.get("symbol", "").upper()
            if symbol and symbol not in BLACKLIST:
                yf_sym = f"{symbol}-USD"
                if yf_sym not in yf_symbols:
                    yf_symbols.append(yf_sym)
                    COIN_RANK[yf_sym] = rank
                    rank += 1
        print(f"Fetched {len(yf_symbols)} coins (blacklist filtered): {', '.join(yf_symbols[:10])}...")
        return yf_symbols[:limit]
    except Exception as e:
        print(f"CoinGecko API failed: {e}. Using fallback list.")
        fallback = [
            "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
            "ADA-USD", "DOGE-USD", "DOT-USD", "MATIC-USD", "LINK-USD",
            "UNI-USD", "AVAX-USD", "LTC-USD", "FIL-USD", "TRX-USD",
            "ATOM-USD", "XLM-USD", "ETC-USD", "BCH-USD", "NEAR-USD",
            "VET-USD", "ICP-USD", "HBAR-USD", "APT-USD", "ARB-USD",
            "OP-USD", "GRT-USD", "THETA-USD", "ALGO-USD", "FTM-USD",
            "EGLD-USD", "IMX-USD", "SAND-USD", "AXS-USD", "MANA-USD",
            "AAVE-USD", "MKR-USD", "SNX-USD", "CRV-USD", "COMP-USD",
            "ZEC-USD", "BAT-USD", "ENJ-USD", "CHZ-USD", "HOT-USD",
            "KSM-USD", "DASH-USD", "CELO-USD", "QTUM-USD", "IOST-USD"
        ]
        COIN_RANK = {sym: i+1 for i, sym in enumerate(fallback)}
        return fallback[:limit]

COIN_RANK = {}
CRYPTO_PAIRS = fetch_top_liquid_coins(50)
print(f"Trading universe: {len(CRYPTO_PAIRS)} coins")

# ========== PORTFOLIO ==========
PORTFOLIO_FILE = "crypto_portfolio.json"

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE) as f:
                data = json.load(f)
            return {
                "balance": data.get("balance", 1000.0),
                "realized_pnl": data.get("realized_pnl", 0.0),
                "open_positions": data.get("open_positions", 0),
                "daily_loss_limit": data.get("daily_loss_limit", -20)
            }
        except:
            pass
    return {
        "balance": 1000.0,
        "realized_pnl": 0.0,
        "open_positions": 0,
        "daily_loss_limit": -20
    }

def save_portfolio(p):
    try:
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(p, f, indent=2)
    except:
        pass

portfolio = load_portfolio()

# ========== CSV LOGGING ==========
TRADE_LOG_CSV = "crypto_trade_log.csv"
OPEN_TRADES_CSV = "crypto_open_trades.csv"
TRADE_RESULTS_CSV = "crypto_trade_results.csv"

def init_csv(f, cols):
    if not os.path.exists(f):
        pd.DataFrame(columns=cols).to_csv(f, index=False)

def append_csv(f, df_new):
    try:
        existing = pd.read_csv(f)
        updated = pd.concat([existing, df_new], ignore_index=True)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        updated = df_new
    updated.to_csv(f, index=False)

def save_csv(f, df):
    df.to_csv(f, index=False)

def initialize_trade_files():
    init_csv(TRADE_LOG_CSV, ["timestamp","symbol","action","entry","stop",
                             "TP1","TP2","TP3","TP4","TP5","score","ai_approved"])
    init_csv(OPEN_TRADES_CSV, ["timestamp","symbol","action","entry","stop",
                               "TP1","TP2","TP3","TP4","TP5","status",
                               "quantity","original_qty","highest_tp","breakeven"])
    init_csv(TRADE_RESULTS_CSV, ["timestamp","symbol","action","entry","stop",
                                 "TP1","TP2","TP3","TP4","TP5","status",
                                 "hit_level","close_time","exit_price","quantity","pnl"])

def log_signal(sig):
    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": sig["symbol"],
        "action": sig["action"],
        "entry": sig["limit_price"],
        "stop": sig["stop_loss"],
        "TP1": sig["take_profits"][0],
        "TP2": sig["take_profits"][1],
        "TP3": sig["take_profits"][2],
        "TP4": sig["take_profits"][3],
        "TP5": sig["take_profits"][4],
        "score": sig["score"],
        "ai_approved": sig.get("ai_approved", False)
    }
    append_csv(TRADE_LOG_CSV, pd.DataFrame([row]))

def add_open_trade(sig):
    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": sig["symbol"],
        "action": sig["action"],
        "entry": sig["limit_price"],
        "stop": sig["stop_loss"],
        "TP1": sig["take_profits"][0],
        "TP2": sig["take_profits"][1],
        "TP3": sig["take_profits"][2],
        "TP4": sig["take_profits"][3],
        "TP5": sig["take_profits"][4],
        "status": "open",
        "quantity": sig["quantity"],
        "original_qty": sig["quantity"],
        "highest_tp": -1,
        "breakeven": False
    }
    append_csv(OPEN_TRADES_CSV, pd.DataFrame([row]))

# ========== PORTFOLIO HELPERS ==========
def daily_pnl():
    try:
        df = pd.read_csv(TRADE_RESULTS_CSV)
        if df.empty:
            return 0.0
        today = datetime.now().strftime("%Y-%m-%d")
        df['close_time'] = pd.to_datetime(df['close_time'])
        daily = df[df['close_time'].dt.strftime("%Y-%m-%d") == today]
        return daily['pnl'].sum() if not daily.empty else 0.0
    except:
        return 0.0

def update_portfolio(trade_result):
    portfolio['balance'] += trade_result['pnl']
    portfolio['realized_pnl'] += trade_result['pnl']
    save_portfolio(portfolio)

# ========== DATA ==========
def get_data(pair, interval='4h', days=14, start=None, end=None):
    ysym = pair
    if start is None:
        end = datetime.now()
        start = end - timedelta(days=days)
    else:
        end = end if end else datetime.now()
    try:
        df = yf.download(ysym, start=start, end=end, interval=interval, progress=False)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except:
        return pd.DataFrame()

def get_total_market_index(interval='4h', days=14):
    try:
        df = yf.download("TOTAL", period=f"{days}d", interval=interval, progress=False)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
    except:
        pass
    return pd.DataFrame()

# ========== TECHNICAL INDICATORS ==========
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def atr(df, period=14):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_val = tr.rolling(period).mean().iloc[-1]
    return atr_val if not pd.isna(atr_val) else None

def rsi(df, period=14):
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi_val = 100 - (100 / (1 + rs)).iloc[-1]
    return rsi_val if not pd.isna(rsi_val) else None

def macd(df):
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    macd_line = exp1 - exp2
    signal = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal
    return (macd_line.iloc[-1], signal.iloc[-1], histogram.iloc[-1],
            histogram.iloc[-2] if len(histogram) > 1 else 0)

def adx(df, period=14):
    h, l, c = df['High'], df['Low'], df['Close']
    dm_plus = h.diff()
    dm_minus = -l.diff()
    dm_plus[dm_plus < 0] = 0
    dm_minus[dm_minus < 0] = 0
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_val = tr.ewm(alpha=1/period, adjust=False).mean()
    di_plus = 100 * (dm_plus.ewm(alpha=1/period, adjust=False).mean() / atr_val)
    di_minus = 100 * (dm_minus.ewm(alpha=1/period, adjust=False).mean() / atr_val)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
    adx_val = dx.ewm(alpha=1/period, adjust=False).mean()
    return adx_val.iloc[-1], di_plus.iloc[-1], di_minus.iloc[-1]

def support_resistance_levels(df, lookback=20):
    recent = df.tail(lookback)
    high = recent['High'].max()
    low = recent['Low'].min()
    return high, low

# ========== MULTI‑LAYER SCORING (0.5R TP1 optimized, failure reporting) ==========
def score_pair(pair):
    layers = {}

    df_d = get_data(pair, interval='1d', days=90)
    if df_d.empty or len(df_d) < 50:
        return 0, None, None, None, None, {"Daily data": (0, 0, "FAIL: insufficient daily candles")}

    df_4h = get_data(pair, interval='4h', days=14)
    if df_4h.empty or len(df_4h) < 50:
        return 0, None, None, None, None, {"4h data": (0, 0, "FAIL: insufficient 4h candles")}

    df_1h = get_data(pair, interval='1h', days=3)
    if df_1h.empty or len(df_1h) < 10:
        return 0, None, None, None, None, {"1h data": (0, 0, "FAIL: insufficient 1h candles")}

    price = df_4h['Close'].iloc[-1]

    ema50_d = ema(df_d['Close'], 50)
    ema200_d = ema(df_d['Close'], 200)
    trend_daily = 0
    if price > ema50_d.iloc[-1] and ema50_d.iloc[-1] > ema200_d.iloc[-1]:
        trend_daily = 1
    elif price < ema50_d.iloc[-1] and ema50_d.iloc[-1] < ema200_d.iloc[-1]:
        trend_daily = -1
    if trend_daily == 0:
        return 0, None, None, None, None, {"Daily trend": (0, 0, "FAIL: no clear daily trend")}

    ema50_4h = ema(df_4h['Close'], 50)
    ema200_4h = ema(df_4h['Close'], 200)
    adx_val, di_plus, di_minus = adx(df_4h)
    rsi_val = rsi(df_4h)
    macd_line, macd_signal, macd_hist, macd_hist_prev = macd(df_4h)
    atr_val = atr(df_4h)
    res, sup = support_resistance_levels(df_4h, 20)

    rsi_1h_val = rsi(df_1h, 14)
    last_candle = df_1h.iloc[-1]
    prev_candle = df_1h.iloc[-2]
    candle_range = last_candle['High'] - last_candle['Low']
    bullish_momentum = (last_candle['Close'] - last_candle['Open']) / candle_range if candle_range > 0 else 0

    vol_last = df_4h['Volume'].iloc[-1]
    vol_avg = df_4h['Volume'].iloc[-6:-1].mean() if len(df_4h) >= 6 else vol_last
    vol_surge = vol_last > vol_avg * 1.2 if vol_avg > 0 else False

    total_df = get_total_market_index(interval='4h', days=14)
    market_aligned = False
    if not total_df.empty and len(total_df) >= 50:
        total_ema50 = ema(total_df['Close'], 50)
        market_trend_up = total_df['Close'].iloc[-1] > total_ema50.iloc[-1]
        if trend_daily == 1 and market_trend_up:
            market_aligned = True
        elif trend_daily == -1 and not market_trend_up:
            market_aligned = True
    else:
        layers["Market"] = (0, 0.5, "FAIL: TOTAL data unavailable")

    def bool_score(cond):
        return 1 if cond else 0

    direction = "LONG" if trend_daily == 1 else "SHORT"

    # EMA Align
    if direction == "LONG":
        ema_align = price > ema50_4h.iloc[-1] and ema50_4h.iloc[-1] > ema200_4h.iloc[-1]
    else:
        ema_align = price < ema50_4h.iloc[-1] and ema50_4h.iloc[-1] < ema200_4h.iloc[-1]
    layers["EMA Align"] = (bool_score(ema_align) * 1.5, 1.5, "OK")

    # ADX
    adx_trending = adx_val > 20
    adx_dir = (di_plus > di_minus) if direction == "LONG" else (di_minus > di_plus)
    layers["ADX"] = (bool_score(adx_trending and adx_dir) * 1.0, 1.0, "OK")

    # RSI
    if rsi_val is not None:
        rsi_score = bool_score((direction == "LONG" and rsi_val > 50) or (direction == "SHORT" and rsi_val < 50))
        layers["RSI"] = (rsi_score * 1.5, 1.5, "OK")
    else:
        layers["RSI"] = (0, 1.5, "FAIL: RSI NaN")

    # MACD
    macd_expanding = (direction == "LONG" and macd_hist > 0 and macd_hist > macd_hist_prev) or \
                     (direction == "SHORT" and macd_hist < 0 and macd_hist < macd_hist_prev)
    layers["MACD"] = (bool_score(macd_expanding) * 1.0, 1.0, "OK")

    # S/R
    if atr_val is not None and atr_val > 0:
        if direction == "LONG":
            near_support = (price - sup) < atr_val * 0.5
            sr_score = bool_score(near_support)
        else:
            near_resistance = (res - price) < atr_val * 0.5
            sr_score = bool_score(near_resistance)
        layers["S/R"] = (sr_score * 1.0, 1.0, "OK")
    else:
        layers["S/R"] = (0, 1.0, "FAIL: ATR missing")

    # Volume
    layers["Volume"] = (bool_score(vol_surge) * 0.5, 0.5, "OK")

    # Market (if not already set as FAIL)
    if "Market" not in layers:
        layers["Market"] = (bool_score(market_aligned) * 0.5, 0.5, "OK")

    # Candle Momentum
    if direction == "LONG":
        candle_ok = bullish_momentum > 0.5
    else:
        candle_ok = bullish_momentum < -0.5
    layers["Candle Mom"] = (bool_score(candle_ok) * 2.0, 2.0, "OK")

    # RSI 1h
    if rsi_1h_val is not None:
        if direction == "LONG":
            rsi_1h_ok = rsi_1h_val < 63
        else:
            rsi_1h_ok = rsi_1h_val > 37
        layers["RSI 1h"] = (bool_score(rsi_1h_ok) * 1.5, 1.5, "OK")
    else:
        layers["RSI 1h"] = (0, 1.5, "FAIL: RSI 1h NaN")

    # ATR
    if atr_val is not None and price > 0:
        atr_ok = atr_val > price * 0.005
        layers["ATR"] = (bool_score(atr_ok) * 1.0, 1.0, "OK")
    else:
        layers["ATR"] = (0, 1.0, "FAIL: ATR missing")

    # Micro Trend
    if direction == "LONG":
        micro_ok = last_candle['Close'] > last_candle['Open'] and prev_candle['Close'] > prev_candle['Open']
    else:
        micro_ok = last_candle['Close'] < last_candle['Open'] and prev_candle['Close'] < prev_candle['Open']
    layers["Micro Trend"] = (bool_score(micro_ok) * 2.0, 2.0, "OK")

    total = sum(score for score, _, _ in layers.values() if isinstance(score, (int, float)))

    return total, direction, price, atr_val, (sup if direction == "LONG" else res), layers

# ========== AI CONFIRMATION GATE ==========
def ai_confirm_trade(signal_dict):
    if not GROQ_API_KEY:
        return True
    sym = signal_dict["symbol"]
    direction = signal_dict["action"]
    entry = signal_dict["limit_price"]
    stop = signal_dict["stop_loss"]
    score = signal_dict["score"]

    prompt = (
        f"Crypto trade setup:\n"
        f"Pair: {sym}\n"
        f"Direction: {direction}\n"
        f"Entry: {entry:.5f}\n"
        f"Stop Loss: {stop:.5f}\n"
        f"Technical Conviction Score: {score:.1f}/13.5\n\n"
        f"Will this trade likely hit TP1 (0.5x the stop distance) before hitting the stop? "
        f"Answer with exactly one word: PASS or FAIL."
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "You are a professional crypto analyst. Respond with only PASS or FAIL."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 5
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"].strip().upper()
            if "FAIL" in text:
                return False
            return True
    except:
        pass
    return True

# ========== SIGNAL GENERATION (WIDER STOP, 0.5R TP1) ==========
def generate_signal():
    open_symbols_risky = set()
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
        if not open_df.empty:
            if "breakeven" in open_df.columns:
                risky = open_df[open_df["breakeven"] == False]
            else:
                risky = open_df
            open_symbols_risky = set(risky["symbol"].values)
    except:
        pass

    all_scored = []
    top_overall = None

    for pair in CRYPTO_PAIRS:
        if pair in open_symbols_risky:
            continue
        score, direction, price, atr_val, swing_level, layers = score_pair(pair)
        if direction is None:
            continue
        all_scored.append((pair, score, direction, price, atr_val, swing_level, layers))
        if top_overall is None or score > top_overall[1]:
            top_overall = (pair, score, direction, price, atr_val, swing_level, layers)

    top5 = sorted(all_scored, key=lambda x: x[1], reverse=True)[:5]
    top_layers = top_overall[6] if top_overall else {}

    candidates = [item for item in all_scored if item[1] >= 6.0]
    if not candidates:
        return None, top5, top_layers

    candidates.sort(key=lambda x: x[1], reverse=True)
    best = candidates[0]
    pair, score, direction, price, atr_val, swing_level, layers = best

    # ---- WIDER STOP LOSS ----
    rank = COIN_RANK.get(pair, 99)
    if rank <= 10:           # Top 10 most liquid
        min_stop_pct = 0.01   # 1.0%
        max_stop_pct = 0.04   # 4.0%
    else:                    # Other coins
        min_stop_pct = 0.02   # 2.0%
        max_stop_pct = 0.06   # 6.0%

    raw_stop = (atr_val * 2.5) if (atr_val is not None and not math.isnan(atr_val)) else price * 0.02
    min_stop = price * min_stop_pct
    max_stop = price * max_stop_pct
    stop_distance = np.clip(raw_stop, min_stop, max_stop)

    if direction == "LONG":
        stop = price - stop_distance
        if swing_level is not None and swing_level > price - stop_distance * 1.2:
            stop = min(stop, swing_level - 0.05 * (atr_val if atr_val else price*0.01))
    else:
        stop = price + stop_distance
        if swing_level is not None and swing_level < price + stop_distance * 1.2:
            stop = max(stop, swing_level + 0.05 * (atr_val if atr_val else price*0.01))

    stop = round(stop, 6)
    risk = abs(price - stop)

    # TP multipliers: 0.5, 1, 2, 3, 5R
    tp_multipliers = [0.5, 1.0, 2.0, 3.0, 5.0]
    tps = [round(price + m * risk, 6) if direction == "LONG" else round(price - m * risk, 6) for m in tp_multipliers]

    risk_amount = portfolio['balance'] * 0.01
    quantity = round(risk_amount / risk, 8)

    signal = {
        "action": direction,
        "symbol": pair,
        "quantity": quantity,
        "limit_price": price,
        "stop_loss": stop,
        "take_profits": tps,
        "score": score,
        "atr": atr_val,
    }

    if not ai_confirm_trade(signal):
        print(f"AI rejected {pair} {direction} (score {score:.1f})")
        return None, top5, top_layers

    signal["ai_approved"] = True
    return signal, top5, top_layers

# ========== TRADE MANAGEMENT (Enhanced with prints) ==========
def check_open_trades():
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        print("No open trades file or empty.")
        return
    if open_df.empty:
        print("Open trades file empty.")
        return

    if "timestamp" in open_df.columns:
        open_df = open_df.sort_values("timestamp").drop_duplicates(subset="symbol", keep="last")
    else:
        open_df = open_df.drop_duplicates(subset="symbol", keep="last")

    for col in ["highest_tp", "quantity", "original_qty", "breakeven"]:
        if col not in open_df.columns:
            if col == "breakeven":
                open_df[col] = False
            elif col == "highest_tp":
                open_df[col] = -1
            else:
                open_df[col] = 0.0

    results = []
    still_open = []
    alerts = []
    now = datetime.now()
    fractions = [0.20, 0.20, 0.20, 0.20, 0.20]

    for idx, trade in open_df.iterrows():
        try:
            sym = trade["symbol"]
            direction = trade["action"]
            entry = float(trade["entry"])
            stop_orig = float(trade["stop"])
            original_qty = float(trade.get("original_qty", trade.get("quantity", 0)))
            remaining_qty = float(trade.get("quantity", original_qty))
            breakeven = trade.get("breakeven", False)

            tps = [float(trade[f"TP{i+1}"]) for i in range(5)]

            try:
                entry_time = datetime.strptime(trade["timestamp"], "%Y-%m-%d %H:%M:%S")
            except:
                still_open.append(trade)
                continue

            df_1h = get_data(sym, interval='1h', start=entry_time, end=now)
            if df_1h.empty:
                still_open.append(trade)
                continue

            highest_tp_idx = int(trade.get("highest_tp", -1))
            current_stop = entry if breakeven else stop_orig

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
                        fraction = fractions[i]
                        exit_qty = original_qty * fraction
                        if exit_qty > remaining_qty:
                            exit_qty = remaining_qty
                        if exit_qty > 0:
                            exit_price = tps[i]
                            pnl = (exit_price - entry) * exit_qty if direction == "LONG" else (entry - exit_price) * exit_qty
                            partial = trade.to_dict()
                            partial["hit_level"] = f"TP{i+1}"
                            partial["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                            partial["exit_price"] = exit_price
                            partial["quantity"] = exit_qty
                            partial["pnl"] = round(pnl, 4)
                            results.append(partial)
                            update_portfolio({'pnl': pnl})
                            remaining_qty -= exit_qty
                            highest_tp_idx = i
                            if i == 0:
                                breakeven = True
                                current_stop = entry
                        alerts.append(f"🚀 {sym} {direction} TP{i+1} hit — {fraction*100:.0f}% closed, SL to BE")
                        send_closed_trade_chart(trade, f"TP{i+1}", exit_price, pnl, remaining_qty)

                    if remaining_qty <= 0:
                        break

                if remaining_qty > 0:
                    sl_hit = (low <= current_stop) if direction == "LONG" else (high >= current_stop)
                    if sl_hit:
                        exit_price = current_stop
                        pnl = (exit_price - entry) * remaining_qty if direction == "LONG" else (entry - exit_price) * remaining_qty
                        final = trade.to_dict()
                        if breakeven:
                            desc = "BREAKEVEN STOP"
                            pnl = 0.0
                        else:
                            desc = "STOP LOSS" if highest_tp_idx == -1 else f"STOP LOSS after TP{highest_tp_idx+1}"
                        final["hit_level"] = desc
                        final["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                        final["exit_price"] = exit_price
                        final["quantity"] = remaining_qty
                        final["pnl"] = round(pnl, 4)
                        results.append(final)
                        update_portfolio({'pnl': pnl})
                        remaining_qty = 0
                        alerts.append(f"🔴 {sym} {direction} → {desc}")
                        send_closed_trade_chart(trade, desc, exit_price, pnl, 0)
                        break

            if remaining_qty > 0:
                trade["highest_tp"] = highest_tp_idx
                trade["quantity"] = remaining_qty
                trade["breakeven"] = breakeven
                still_open.append(trade)
        except Exception as e:
            print(f"Error processing trade {trade.get('symbol','?')}: {e}")

    if results:
        append_csv(TRADE_RESULTS_CSV, pd.DataFrame(results))
    if still_open:
        save_csv(OPEN_TRADES_CSV, pd.DataFrame(still_open))
        portfolio['open_positions'] = len(still_open)
    else:
        save_csv(OPEN_TRADES_CSV, pd.DataFrame())
        portfolio['open_positions'] = 0
    save_portfolio(portfolio)

    if alerts:
        print("ALERTS:", alerts)
        send_telegram("Crypto trade updates:\n" + "\n".join(alerts))
    else:
        print("No trade closures this run.")

# ========== CHART ON TRADE CLOSE ==========
def send_closed_trade_chart(trade, hit_level, exit_price, pnl, remaining_qty):
    sym = trade["symbol"]
    entry = float(trade["entry"])
    stop = float(trade["stop"])
    tps = [float(trade[f"TP{i+1}"]) for i in range(5)]
    direction = trade["action"]

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import mplfinance as mpf

        entry_time = datetime.strptime(trade["timestamp"], "%Y-%m-%d %H:%M:%S")
        df = get_data(sym, interval='1h', start=entry_time, end=datetime.now())
        if df.empty:
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

        title = f"{sym} {direction} – {hit_level} (PnL: {pnl:.2f}$)"
        fig, ax = mpf.plot(df, type='candle', style=mpf_style,
                           title=title, ylabel='Price',
                           returnfig=True, figsize=(8,6))

        ax.axhline(y=entry, color='#f1c40f', linestyle='--', linewidth=1.5, label='Entry')
        ax.axhline(y=stop, color='#e74c3c', linestyle='--', linewidth=1.5, label='Stop')
        for i, tp in enumerate(tps):
            ax.axhline(y=tp, color='#2ecc71', linestyle='--', linewidth=1, alpha=0.6,
                       label=f'TP{i+1}' if i==0 else None)
        ax.axhline(y=exit_price, color='#e67e22', linewidth=2, label=f'Exit ({hit_level})')
        ax.legend(loc='upper left', facecolor='#000000', edgecolor='white', labelcolor='white')

        chart_path = f"{sym}_close_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        fig.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor='black')
        plt.close(fig)

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(chart_path, 'rb') as img:
            requests.post(url, data={'chat_id': CHAT_ID}, files={'photo': img})
        os.remove(chart_path)
    except Exception as e:
        print(f"Closed trade chart error: {e}")

# ========== TELEGRAM ==========
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

# ========== SIGNAL FORMATTING (Elite 7-Angle Binance Square) ==========
def format_signal(sig):
    sym = sig["symbol"].replace("-USD", "")
    cashtag = f"${sym}"
    direction = sig["action"]
    entry = sig["limit_price"]
    stop = sig["stop_loss"]
    tps = sig["take_profits"]
    score = sig["score"]
    risk = abs(entry - stop)
    stop_pct = risk / entry * 100

    angle = random.randint(1, 7)

    if angle == 1:
        if direction == "LONG":
            hook = f"{cashtag} just swept the 4H lows and printed a massive rejection wick – someone got trapped short, and the bounce is real."
            story = (
                f"Price wicked below recent support, triggering a classic stop‑hunt. "
                f"The volume spike on the reversal candle screams aggressive buyer absorption. "
                f"With $BTC holding above its 4H demand zone, this sweep‑and‑reclaim pattern has a high probability of follow‑through."
            )
        else:
            hook = f"{cashtag} just tapped the 4H resistance and got violently rejected – late longs are now trapped above, fueling the dump."
            story = (
                f"The upper liquidity pool got tested and immediately rejected with rising volume. "
                f"Sellers absorbed the ask side, leaving a bearish engulfing candle. "
                f"$BTC is also showing weakness, confirming the bearish pressure across the board."
            )
    elif angle == 2:
        if direction == "LONG":
            hook = f"{cashtag} is respecting a textbook higher‑low structure – the 4H trend continues to build bullish momentum."
            story = (
                f"Price bounced cleanly off the flipped support zone, maintaining the sequence of higher highs and higher lows. "
                f"The 4H EMAs are stacked bullishly, and $BTC is also riding a strong uptrend, giving this setup structural alignment."
            )
        else:
            hook = f"{cashtag} just broke below the 4H higher‑low and closed a bearish structure shift – lower lows are loading."
            story = (
                f"The previous support has flipped to resistance, and the 4H trend is now making lower highs. "
                f"With $BTC also losing its 4H market structure, this bearish continuation looks technically solid."
            )
    elif angle == 3:
        if direction == "LONG":
            hook = f"{cashtag} is coiling inside a tight range – EMAs are squeezing, and a volume explosion off the whale defense zone is imminent."
            story = (
                f"Price has compressed to the apex of a tightening range, with EMAs flattening into a tight band. "
                f"Whale‑sized limit orders are defending the lower boundary, visible on the volume profile. "
                f"$BTC is also consolidating, ready to expand – an aggressive expansion toward the upside is likely."
            )
        else:
            hook = f"{cashtag} is trapped inside a descending compression – the EMAs are crimping, and volume is drying up ahead of a breakdown."
            story = (
                f"Price is hugging the upper boundary of a falling consolidation, with EMAs turning into resistance. "
                f"A massive sell‑side volume delta is building, suggesting whales are reloading shorts. "
                f"$BTC’s sideways limp adds weight to this bearish compression play."
            )
    elif angle == 4:
        if direction == "LONG":
            hook = f"{cashtag} is seeing massive passive bids absorbing every sell at the 4H demand block – institutions are loading."
            story = (
                f"Limit orders are stacked at this weekly support, absorbing all market sell pressure without breaking lower. "
                f"This is classic institutional absorption. $BTC's steady bid across the board supports this accumulation thesis."
            )
        else:
            hook = f"{cashtag} is being heavily distributed at the 4H supply zone – passive sellers are capping every rally."
            story = (
                f"Ask-side walls are absorbing buying pressure at the resistance, preventing any breakout. "
                f"This distribution behavior, combined with $BTC's weakening trend, signals a potential dump."
            )
    elif angle == 5:
        if direction == "LONG":
            hook = f"{cashtag} left a Fair Value Gap below – price is magnetically drawing back to fill it before the next leg up."
            story = (
                f"Price delivered inefficiently, leaving a clear FVG that has yet to be filled. "
                f"The pullback is likely a rebalancing move before continuation. $BTC’s bullish structure supports the fill‑and‑rip scenario."
            )
        else:
            hook = f"{cashtag} left an overhead FVG – price is inefficient there and will likely rally to fill it before the dump resumes."
            story = (
                f"The imbalance above is acting as a price magnet. A retracement to fill the gap before the bearish continuation is probable. "
                f"$BTC is also correcting, reinforcing the fill‑then‑fall outlook."
            )
    elif angle == 6:
        if direction == "LONG":
            hook = f"{cashtag} is perfectly synced: the 4H bull flag is aligning with the daily EMA breakout."
            story = (
                f"The 4H consolidation is resolving in the direction of the daily trend, a high‑probability continuation signal. "
                f"$BTC’s macro structure is confirming the bullish regime, increasing the odds of a clean breakout."
            )
        else:
            hook = f"{cashtag} is forming a 4H bear flag while the daily trend flips bearish – confluences are stacking for a breakdown."
            story = (
                f"Lower timeframe indecision is aligning with a macro trend shift to the downside. "
                f"With $BTC also breaking key levels, this bearish confluence is extremely powerful."
            )
    else:  # angle == 7
        if direction == "LONG":
            hook = f"{cashtag} bears are exhausted – the 4H selling pressure just dried up at a key support."
            story = (
                f"Sellers attempted to push lower but produced only small bodies and long lower wicks, showing no follow‑through. "
                f"This exhaustion is a classic reversal signal, especially as $BTC starts to show a potential bounce."
            )
        else:
            hook = f"{cashtag} bulls are out of steam – the 4H rally just printed a wick-heavy doji at resistance."
            story = (
                f"Buyers are failing to push higher, producing overlapping candles with shrinking volume. "
                f"This exhaustion at the resistance zone, coupled with $BTC's weakness, points to a bearish reversal."
            )

    tp_str = " / ".join([f"{tp:.5f}" for tp in tps])

    msg = (
        f"🪝 {hook}\n\n"
        f"📈 Price Action Breakdown:\n"
        f"{story}\n\n"
        f"🟢 Execution Framework:\n"
        f"• Area of Interest: {entry:.5f}\n"
        f"• Technical Invalidation: {stop:.5f} (-{stop_pct:.2f}%)\n"
        f"• Target Objectives: {tp_str}\n\n"
        f"💬 Are you taking this setup or fading it? Drop your bias below! 👇\n\n"
        f"#TradingRationale #PriceAction #{sym.upper()} #BinanceSquare\n\n"
        f"*Disclaimer: This price action analysis is for educational purposes only. Not financial advice. "
        f"Always practice strict risk management and DYOR.*"
    )

    return msg

# ========== HOLD MESSAGE FORMATTING ==========
def format_hold_message(top5, top_layers):
    if not top5:
        return "HOLD – No valid trade setups found. Market is fully trendless."

    lines = ["HOLD – No high‑conviction crypto setup found.\n📊 **Top Coin Scores** (of {})".format(len(top5))]
    for idx, (pair, score, direction, _, _, _, _) in enumerate(top5, 1):
        short = pair.replace("-USD", "")
        lines.append(f"{idx}. {short} → {direction} ({score:.1f}/13.5)")

    if top_layers:
        top_pair = top5[0][0].replace("-USD", "")
        top_score = top5[0][1]
        top_dir = top5[0][2]
        lines.append(f"\n🔎 **Top Coin Layer Breakdown:** {top_pair} ({top_dir}, {top_score:.1f})")
        for name, (earned, max_, status) in top_layers.items():
            if "FAIL" in status:
                lines.append(f"• {name} ({max_}): ⚠️ {status}")
            else:
                check = "✅" if earned > 0 else "❌"
                lines.append(f"• {name} ({max_}): {check}")
    else:
        lines.append("\nNo layer data available.")

    lines.append("\n💬 Are you stalking any setups? Drop your watchlist below! 👇")
    return "\n".join(lines)

# ========== CHART ON SIGNAL ==========
def send_trade_chart(signal):
    sym = signal['symbol']
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import mplfinance as mpf

        df = get_data(sym, interval='4h', days=21)
        if df.empty or len(df) < 20:
            raise ValueError(f"Only {len(df)} 4h candles")

        mpf_style = mpf.make_mpf_style(
            base_mpf_style='nightclouds',
            facecolor='#000000',
            gridcolor='#2a2e39',
            rc={'axes.labelcolor': 'white',
                'xtick.color': 'white',
                'ytick.color': 'white',
                'axes.titlecolor': 'white'}
        )

        ema50 = df['Close'].ewm(span=min(50, len(df)), adjust=False).mean()
        addplots = [mpf.make_addplot(ema50, color='#f39c12', width=1.5, label='EMA50')]
        if df['Volume'].sum() > 0:
            typical = (df['High'] + df['Low'] + df['Close']) / 3
            vwap = (typical * df['Volume']).cumsum() / df['Volume'].cumsum()
            addplots.append(mpf.make_addplot(vwap, color='#3498db', width=1, linestyle='--', label='VWAP'))

        fig, axes = mpf.plot(df, type='candle', style=mpf_style,
                             title=f"{sym} 4h", ylabel='Price', addplot=addplots,
                             returnfig=True, figsize=(8,6))
        ax = axes[0]
        entry = signal.get('limit_price')
        stop = signal.get('stop_loss')
        tps = signal.get('take_profits')
        if entry:
            ax.axhline(y=entry, color='#f1c40f', linestyle='--', linewidth=1.5, label='Entry')
            ax.axhline(y=stop, color='#e74c3c', linestyle='--', linewidth=1.5, label='Stop')
            if tps:
                for i, tp in enumerate(tps):
                    ax.axhline(y=tp, color='#2ecc71', linestyle='--', linewidth=1, alpha=0.8,
                               label=f'TP{i+1}' if i==0 else None)
            ax.legend(loc='upper left', facecolor='#000000', edgecolor='white', labelcolor='white')

        chart_path = f"{sym}_chart.png"
        fig.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor='black')
        plt.close(fig)

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(chart_path, 'rb') as img:
            requests.post(url, data={'chat_id': CHAT_ID}, files={'photo': img})
        os.remove(chart_path)
    except Exception as e:
        print(f"Chart image error: {e}")
        studies = "&studies[]=STD%3BEMA%3B50&studies[]=STD%3BVWAP"
        tv_url = f"https://www.tradingview.com/chart/?symbol={sym.replace('-','')}&interval=240{studies}"
        send_telegram(f"📈 Chart unavailable – view here: {tv_url}")

# ========== MAIN ==========
def main():
    try:
        initialize_trade_files()
        check_open_trades()

        # Show current open trade count in log
        try:
            open_df = pd.read_csv(OPEN_TRADES_CSV)
            print(f"Currently {len(open_df)} open trade(s).")
        except:
            print("No open trades file found.")

        if daily_pnl() <= portfolio['daily_loss_limit']:
            send_telegram("Daily loss limit reached. No new trades today.")
            return

        sig, top5, top_layers = generate_signal()
        if sig:
            log_signal(sig)
            add_open_trade(sig)
            portfolio['open_positions'] += 1
            save_portfolio(portfolio)
            send_telegram(format_signal(sig))
            send_trade_chart(sig)
        else:
            hold_msg = format_hold_message(top5, top_layers)
            send_telegram(hold_msg)
    except Exception as e:
        err = f"Bot crashed: {traceback.format_exc()[:500]}"
        print(err)
        send_telegram(err)

if __name__ == "__main__":
    main()