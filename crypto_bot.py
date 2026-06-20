#!/usr/bin/env python3
"""
Crypto Swing Bot – Binance data, 0.5R TP1, 5 TPs (0.5/1/2/3/5R)
Wider stop (2.5x ATR, 1.0‑6.0%) for swing tolerance.
Compact Discord signals + full alert system.
Position splitting: 30%/10%/10%/10%/40%. Trailing stop logic.
BLACKLIST for stablecoins & QUQ.
All price data from Binance public klines (reliable 1h).
Automatic conversion of old YFinance symbols to Binance format.
"""

import requests, json, os, traceback, random, math
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# ========== ENVIRONMENT ==========
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    print("WARNING: GROQ_API_KEY not set – AI filtering disabled.")

# ========== BLACKLIST ==========
BLACKLIST = {
    "QUQ", "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "FDUSD"
}

# ========== TIME HELPERS (Strict UTC Engine) ==========
def get_now():
    """Returns naive UTC datetime matching Binance server standards."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

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
    headers = {
        "accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()
        symbols = []
        COIN_RANK = {}
        rank = 1
        for coin in data:
            symbol = coin.get("symbol", "").upper()
            if symbol and symbol not in BLACKLIST:
                sym = f"{symbol}USDT"      # Binance pair format
                if sym not in symbols:
                    symbols.append(sym)
                    COIN_RANK[sym] = rank
                    rank += 1
        print(f"Fetched {len(symbols)} coins (blacklist filtered)")
        return symbols[:limit]
    except Exception as e:
        print(f"CoinGecko API failed: {e}. Using fallback.")
        fallback = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
                    "ADAUSDT","DOGEUSDT","DOTUSDT","MATICUSDT","LINKUSDT"]
        COIN_RANK = {sym: i+1 for i, sym in enumerate(fallback)}
        return fallback[:limit]

COIN_RANK = {}
CRYPTO_PAIRS = fetch_top_liquid_coins(50)

# ========== PORTFOLIO ==========
PORTFOLIO_FILE = "crypto_portfolio.json"
def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE) as f: data = json.load(f)
            return {
                "balance": data.get("balance", 1000.0),
                "realized_pnl": data.get("realized_pnl", 0.0),
                "open_positions": data.get("open_positions", 0),
                "daily_loss_limit": data.get("daily_loss_limit", -20)
            }
        except: pass
    return {"balance": 1000.0, "realized_pnl": 0.0, "open_positions": 0, "daily_loss_limit": -20}

def save_portfolio(p):
    try:
        with open(PORTFOLIO_FILE, "w") as f: json.dump(p, f, indent=2)
    except: pass

portfolio = load_portfolio()

# ========== CSV LOGGING ==========
TRADE_LOG_CSV = "crypto_trade_log.csv"
OPEN_TRADES_CSV = "crypto_open_trades.csv"
TRADE_RESULTS_CSV = "crypto_trade_results.csv"

def init_csv(f, cols):
    if not os.path.exists(f): pd.DataFrame(columns=cols).to_csv(f, index=False)

def append_csv(f, df_new):
    try:
        existing = pd.read_csv(f)
        updated = pd.concat([existing, df_new], ignore_index=True)
    except: updated = df_new
    updated.to_csv(f, index=False)

def save_csv(f, df): df.to_csv(f, index=False)

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
    row = {"timestamp": get_now().strftime("%Y-%m-%d %H:%M:%S"),
           "symbol": sig["symbol"], "action": sig["action"],
           "entry": sig["limit_price"], "stop": sig["stop_loss"],
           "TP1": sig["take_profits"][0], "TP2": sig["take_profits"][1],
           "TP3": sig["take_profits"][2], "TP4": sig["take_profits"][3],
           "TP5": sig["take_profits"][4], "score": sig["score"],
           "ai_approved": sig.get("ai_approved", False)}
    append_csv(TRADE_LOG_CSV, pd.DataFrame([row]))

def add_open_trade(sig):
    row = {"timestamp": get_now().strftime("%Y-%m-%d %H:%M:%S"),
           "symbol": sig["symbol"], "action": sig["action"],
           "entry": sig["limit_price"], "stop": sig["stop_loss"],
           "TP1": sig["take_profits"][0], "TP2": sig["take_profits"][1],
           "TP3": sig["take_profits"][2], "TP4": sig["take_profits"][3],
           "TP5": sig["take_profits"][4], "status": "open",
           "quantity": sig["quantity"], "original_qty": sig["quantity"],
           "highest_tp": -1, "breakeven": False}
    append_csv(OPEN_TRADES_CSV, pd.DataFrame([row]))

# ========== PORTFOLIO HELPERS ==========
def daily_pnl():
    try:
        df = pd.read_csv(TRADE_RESULTS_CSV)
        if df.empty: return 0.0
        today = get_now().strftime("%Y-%m-%d")
        df['close_time'] = pd.to_datetime(df['close_time'])
        daily = df[df['close_time'].dt.strftime("%Y-%m-%d") == today]
        return daily['pnl'].sum() if not daily.empty else 0.0
    except: return 0.0

def update_portfolio(trade_result):
    portfolio['balance'] += trade_result['pnl']
    portfolio['realized_pnl'] += trade_result['pnl']
    save_portfolio(portfolio)

# ========== BINANCE DATA FETCH (With Multi-Endpoint & Pagination Pagination) ==========
def get_binance_klines(symbol, interval, limit=100, start_time=None, end_time=None):
    """
    Fetch klines from Binance using redundant public endpoints and auto-pagination.
    """
    endpoints = [
        "https://api.binance.com/api/v3/klines",
        "https://api1.binance.com/api/v3/klines",
        "https://api2.binance.com/api/v3/klines",
        "https://api3.binance.com/api/v3/klines"
    ]
    
    # If a precise historical window is supplied, handle candle chunking sequentially
    if start_time:
        current_start = int(start_time.timestamp() * 1000) if isinstance(start_time, datetime) else int(start_time)
        target_end = int(end_time.timestamp() * 1000) if isinstance(end_time, datetime) else int(get_now().timestamp() * 1000)
        
        all_data = []
        while current_start < target_end:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": current_start,
                "endTime": target_end,
                "limit": 1000
            }
            success = False
            for url in endpoints:
                try:
                    resp = requests.get(url, params=params, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            all_data.extend(data)
                            current_start = data[-1][0] + 1  # Shift past the last candle open timestamp
                            success = True
                            break
                        else:
                            current_start = target_end
                            success = True
                            break
                except Exception:
                    continue
            if not success:
                break  # Fail-soft exit to prevent runaway infinite retry loop
                
        if not all_data:
            return pd.DataFrame()
        raw_candles = all_data
    else:
        # Standard processing for current market context queries
        raw_candles = None
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        for url in endpoints:
            try:
                resp = requests.get(url, params=params, timeout=10)
                if resp.status_code == 200:
                    raw_candles = resp.json()
                    if isinstance(raw_candles, list) and len(raw_candles) > 0:
                        break
            except Exception:
                continue
        if not raw_candles or not isinstance(raw_candles, list):
            return pd.DataFrame()

    df = pd.DataFrame(raw_candles, columns=[
        'open_time','Open','High','Low','Close','Volume',
        'close_time','quote_vol','trades','taker_buy_base','taker_buy_quote','ignore'
    ])
    df.drop_duplicates(subset=['open_time'], inplace=True)
    df['Open'] = df['Open'].astype(float)
    df['High'] = df['High'].astype(float)
    df['Low'] = df['Low'].astype(float)
    df['Close'] = df['Close'].astype(float)
    df['Volume'] = df['Volume'].astype(float)
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df.set_index('open_time', inplace=True)
    return df[['Open','High','Low','Close','Volume']]

def get_btc_klines(interval='4h', days=14):
    """Fetch BTC/USDT klines for market context."""
    return get_binance_klines("BTCUSDT", interval, limit=100)

# ========== SYMBOL CONVERTER (for old open trades) ==========
def convert_to_binance(sym):
    """Convert old YFinance format like 'XLM-USD' to Binance 'XLMUSDT'."""
    sym = str(sym).replace("-USD", "USDT")
    if not sym.endswith("USDT"):
        sym = sym + "USDT"
    return sym

# ========== TECHNICAL INDICATORS ==========
def ema(series, period): return series.ewm(span=period, adjust=False).mean()

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
    dm_plus = h.diff(); dm_minus = -l.diff()
    dm_plus[dm_plus < 0] = 0; dm_minus[dm_minus < 0] = 0
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_val = tr.ewm(alpha=1/period, adjust=False).mean()
    di_plus = 100 * (dm_plus.ewm(alpha=1/period, adjust=False).mean() / atr_val)
    di_minus = 100 * (dm_minus.ewm(alpha=1/period, adjust=False).mean() / atr_val)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
    adx_val = dx.ewm(alpha=1/period, adjust=False).mean()
    return adx_val.iloc[-1], di_plus.iloc[-1], di_minus.iloc[-1]

def support_resistance_levels(df, lookback=20):
    recent = df.tail(lookback)
    return recent['High'].max(), recent['Low'].min()

# ========== SCORING (uses Binance data) ==========
def score_pair(pair):
    layers = {}
    df_d = get_binance_klines(pair, '1d', limit=90)
    if df_d.empty or len(df_d) < 50: return 0, None, None, None, None, {"Daily data": (0,0,"FAIL: insufficient daily candles")}
    df_4h = get_binance_klines(pair, '4h', limit=100)
    if df_4h.empty or len(df_4h) < 50: return 0, None, None, None, None, {"4h data": (0,0,"FAIL: insufficient 4h candles")}
    df_1h = get_binance_klines(pair, '1h', limit=72)
    if df_1h.empty or len(df_1h) < 10: return 0, None, None, None, None, {"1h data": (0,0,"FAIL: insufficient 1h candles")}
    price = df_4h['Close'].iloc[-1]
    ema50_d = ema(df_d['Close'], 50); ema200_d = ema(df_d['Close'], 200)
    trend_daily = 0
    if price > ema50_d.iloc[-1] and ema50_d.iloc[-1] > ema200_d.iloc[-1]: trend_daily = 1
    elif price < ema50_d.iloc[-1] and ema50_d.iloc[-1] < ema200_d.iloc[-1]: trend_daily = -1
    if trend_daily == 0: return 0, None, None, None, None, {"Daily trend": (0,0,"FAIL: no clear daily trend")}
    ema50_4h = ema(df_4h['Close'], 50); ema200_4h = ema(df_4h['Close'], 200)
    adx_val, di_plus, di_minus = adx(df_4h)
    rsi_val = rsi(df_4h)
    macd_line, macd_signal, macd_hist, macd_hist_prev = macd(df_4h)
    atr_val = atr(df_4h)
    res, sup = support_resistance_levels(df_4h, 20)
    rsi_1h_val = rsi(df_1h, 14)
    last_candle = df_1h.iloc[-1]; prev_candle = df_1h.iloc[-2]
    candle_range = last_candle['High'] - last_candle['Low']
    bullish_momentum = (last_candle['Close'] - last_candle['Open']) / candle_range if candle_range > 0 else 0
    vol_last = df_4h['Volume'].iloc[-1]
    vol_avg = df_4h['Volume'].iloc[-6:-1].mean() if len(df_4h) >= 6 else vol_last
    vol_surge = vol_last > vol_avg * 1.2 if vol_avg > 0 else False

    # BTC market context (Binance)
    btc_df = get_btc_klines('4h', days=14)
    market_aligned = False
    if not btc_df.empty and len(btc_df) >= 50:
        btc_ema50 = ema(btc_df['Close'], 50)
        btc_trend_up = btc_df['Close'].iloc[-1] > btc_ema50.iloc[-1]
        if trend_daily == 1 and btc_trend_up: market_aligned = True
        elif trend_daily == -1 and not btc_trend_up: market_aligned = True
    else:
        layers["Market"] = (0, 0.5, "FAIL: BTC data unavailable")

    def bool_score(cond): return 1 if cond else 0
    direction = "LONG" if trend_daily == 1 else "SHORT"

    # Layers
    if direction == "LONG": ema_align = price > ema50_4h.iloc[-1] and ema50_4h.iloc[-1] > ema200_4h.iloc[-1]
    else: ema_align = price < ema50_4h.iloc[-1] and ema50_4h.iloc[-1] < ema200_4h.iloc[-1]
    layers["EMA Align"] = (bool_score(ema_align) * 1.5, 1.5, "OK")
    adx_trending = adx_val > 20
    adx_dir = (di_plus > di_minus) if direction == "LONG" else (di_minus > di_plus)
    layers["ADX"] = (bool_score(adx_trending and adx_dir) * 1.0, 1.0, "OK")
    if rsi_val is not None: layers["RSI"] = (bool_score((direction=="LONG" and rsi_val>50) or (direction=="SHORT" and rsi_val<50)) * 1.5, 1.5, "OK")
    else: layers["RSI"] = (0, 1.5, "FAIL: RSI NaN")
    macd_expanding = (direction=="LONG" and macd_hist>0 and macd_hist>macd_hist_prev) or (direction=="SHORT" and macd_hist<0 and macd_hist<macd_hist_prev)
    layers["MACD"] = (bool_score(macd_expanding) * 1.0, 1.0, "OK")
    if atr_val and atr_val>0:
        if direction=="LONG": sr_score = bool_score((price-sup) < atr_val*0.5)
        else: sr_score = bool_score((res-price) < atr_val*0.5)
        layers["S/R"] = (sr_score*1.0, 1.0, "OK")
    else: layers["S/R"] = (0, 1.0, "FAIL: ATR missing")
    layers["Volume"] = (bool_score(vol_surge)*0.5, 0.5, "OK")
    if "Market" not in layers: layers["Market"] = (bool_score(market_aligned)*0.5, 0.5, "OK")
    candle_ok = (bullish_momentum > 0.5) if direction=="LONG" else (bullish_momentum < -0.5)
    layers["Candle Mom"] = (bool_score(candle_ok)*2.0, 2.0, "OK")
    if rsi_1h_val is not None:
        rsi_1h_ok = (rsi_1h_val < 63) if direction=="LONG" else (rsi_1h_val > 37)
        layers["RSI 1h"] = (bool_score(rsi_1h_ok)*1.5, 1.5, "OK")
    else: layers["RSI 1h"] = (0, 1.5, "FAIL: RSI 1h NaN")
    if atr_val and price>0: layers["ATR"] = (bool_score(atr_val > price*0.005)*1.0, 1.0, "OK")
    else: layers["ATR"] = (0, 1.0, "FAIL: ATR missing")
    if direction=="LONG": micro_ok = last_candle['Close'] > last_candle['Open'] and prev_candle['Close'] > prev_candle['Open']
    else: micro_ok = last_candle['Close'] < last_candle['Open'] and prev_candle['Close'] < prev_candle['Open']
    layers["Micro Trend"] = (bool_score(micro_ok)*2.0, 2.0, "OK")
    total = sum(score for score,_,_ in layers.values() if isinstance(score,(int,float)))
    return total, direction, price, atr_val, (sup if direction=="LONG" else res), layers

# ========== AI CONFIRMATION GATE ==========
def ai_confirm_trade(signal_dict):
    if not GROQ_API_KEY: return True
    prompt = (f"Crypto trade setup:\nPair: {signal_dict['symbol']}\nDirection: {signal_dict['action']}\n"
              f"Entry: {signal_dict['limit_price']:.5f}\nStop: {signal_dict['stop_loss']:.5f}\n"
              f"Score: {signal_dict['score']:.1f}/13.5\n"
              f"Will this trade likely hit TP1 (0.5x the stop distance) before hitting the stop? Answer PASS or FAIL.")
    try:
        resp = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [
                {"role":"system","content":"You are a professional crypto analyst. Respond with only PASS or FAIL."},
                {"role":"user","content": prompt}], "temperature":0.1, "max_tokens":5}, timeout=15)
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"].strip().upper()
            return "FAIL" not in text
    except: pass
    return True

# ========== SIGNAL GENERATION ==========
def generate_signal():
    open_symbols_risky = set()
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
        if not open_df.empty:
            if "symbol" in open_df.columns:
                open_df["symbol"] = open_df["symbol"].apply(convert_to_binance)
            if "breakeven" in open_df.columns:
                risky = open_df[open_df["breakeven"].astype(str).str.upper() != "TRUE"]
            else:
                risky = open_df
            open_symbols_risky = set(risky["symbol"].values)
    except: pass
    all_scored = []; top_overall = None
    for pair in CRYPTO_PAIRS:
        if pair in open_symbols_risky: continue
        score, direction, price, atr_val, swing_level, layers = score_pair(pair)
        if direction is None: continue
        all_scored.append((pair, score, direction, price, atr_val, swing_level, layers))
        if top_overall is None or score > top_overall[1]:
            top_overall = (pair, score, direction, price, atr_val, swing_level, layers)
    top5 = sorted(all_scored, key=lambda x: x[1], reverse=True)[:5]
    top_layers = top_overall[6] if top_overall else {}
    candidates = [item for item in all_scored if item[1] >= 6.0]
    if not candidates: return None, top5, top_layers
    candidates.sort(key=lambda x: x[1], reverse=True)
    pair, score, direction, price, atr_val, swing_level, layers = candidates[0]
    rank = COIN_RANK.get(pair, 99)
    if rank <= 10: min_stop_pct, max_stop_pct = 0.01, 0.04
    else: min_stop_pct, max_stop_pct = 0.02, 0.06
    raw_stop = (atr_val * 2.5) if (atr_val is not None and not math.isnan(atr_val)) else price * 0.02
    stop_distance = np.clip(raw_stop, price*min_stop_pct, price*max_stop_pct)
    if direction == "LONG":
        stop = price - stop_distance
        if swing_level and swing_level > price - stop_distance*1.2:
            stop = min(stop, swing_level - 0.05*(atr_val if atr_val else price*0.01))
    else:
        stop = price + stop_distance
        if swing_level and swing_level < price + stop_distance*1.2:
            stop = max(stop, swing_level + 0.05*(atr_val if atr_val else price*0.01))
    stop = round(stop, 6)
    risk = abs(price - stop)
    tp_multipliers = [0.5, 1.0, 2.0, 3.0, 5.0]
    tps = [round(price + m*risk, 6) if direction=="LONG" else round(price - m*risk, 6) for m in tp_multipliers]
    quantity = round((portfolio['balance']*0.01) / risk, 8)
    signal = {"action": direction, "symbol": pair, "quantity": quantity,
              "limit_price": price, "stop_loss": stop, "take_profits": tps,
              "score": score, "atr": atr_val}
    if not ai_confirm_trade(signal):
        print(f"AI rejected {pair} {direction} (score {score:.1f})")
        return None, top5, top_layers
    signal["ai_approved"] = True
    return signal, top5, top_layers

# ========== DISCORD HELPERS ==========
def send_discord_message(text):
    if not DISCORD_WEBHOOK_URL: return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": text[:2000]}, timeout=10)
    except Exception as e: print("Discord text error:", e)

def send_discord_image(image_path, caption=""):
    if not DISCORD_WEBHOOK_URL or not os.path.exists(image_path):
        return
    try:
        with open(image_path, 'rb') as img:
            files = {'file': img}
            payload = {'content': caption[:2000]} if caption else {}
            resp = requests.post(DISCORD_WEBHOOK_URL, data=payload, files=files, timeout=15)
            print(f"Image sent, status: {resp.status_code}")
    except Exception as e: print("Discord image error:", e)

# ========== TRAILING STOP ==========
def get_current_stop(trade):
    entry = float(trade["entry"]); stop_orig = float(trade["stop"])
    tps = [float(trade[f"TP{i+1}"]) for i in range(5)]
    highest_tp_idx = int(trade.get("highest_tp", -1))
    breakeven = str(trade.get("breakeven", "False")).upper() == "TRUE"
    if not breakeven and highest_tp_idx == -1: return stop_orig
    if highest_tp_idx >= 0:
        if highest_tp_idx == 0: return entry
        elif highest_tp_idx == 1: return tps[0]
        elif highest_tp_idx == 2: return tps[1]
        elif highest_tp_idx >= 3: return tps[2]
    return stop_orig

# ========== TRADE MANAGEMENT (with dynamic pagination catch-up) ==========
def check_open_trades():
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
    except: return
    if open_df.empty: return

    if "symbol" in open_df.columns:
        open_df["symbol"] = open_df["symbol"].apply(convert_to_binance)
        save_csv(OPEN_TRADES_CSV, open_df)

    for col in ["highest_tp","quantity","original_qty","breakeven"]:
        if col not in open_df.columns:
            open_df[col] = -1 if col=="highest_tp" else (False if col=="breakeven" else 0.0)

    results = []; still_open = []; alerts = []
    now = get_now()
    fractions = [0.30, 0.10, 0.10, 0.10, 0.40]

    for idx, trade in open_df.iterrows():
        try:
            sym = trade["symbol"]
            direction = trade["action"]
            entry = float(trade["entry"])
            original_qty = float(trade.get("original_qty", trade.get("quantity",0)))
            remaining_qty = float(trade.get("quantity", original_qty))
            tps = [float(trade[f"TP{i+1}"]) for i in range(5)]
            try: entry_time = datetime.strptime(trade["timestamp"], "%Y-%m-%d %H:%M:%S")
            except: still_open.append(trade); continue
            
            # This handles multi-week histories seamlessly via the update pagination loop
            df_1h = get_binance_klines(sym, '1h', start_time=entry_time, end_time=now)
            if df_1h.empty:
                print(f"WARNING: No Binance 1h data for {sym} from {entry_time} to {now}. Trade not checked.")
                still_open.append(trade)
                continue
            highest_tp_idx = int(trade.get("highest_tp", -1))
            current_stop = get_current_stop(trade)
            trade_closed = False
            for candle_time, candle in df_1h.iterrows():
                high = candle['High']; low = candle['Low']
                new_tp_idx = None
                if direction == "LONG":
                    for i in range(len(tps)-1,-1,-1):
                        if high >= tps[i] and i > highest_tp_idx: new_tp_idx = i; break
                else:
                    for i in range(len(tps)-1,-1,-1):
                        if low <= tps[i] and i > highest_tp_idx: new_tp_idx = i; break
                if new_tp_idx is not None:
                    for i in range(highest_tp_idx+1, new_tp_idx+1):
                        if remaining_qty <= 0: break
                        fraction = fractions[i]; exit_qty = original_qty * fraction
                        if exit_qty > remaining_qty: exit_qty = remaining_qty
                        if exit_qty > 0:
                            exit_price = tps[i]
                            pnl = (exit_price - entry) * exit_qty if direction=="LONG" else (entry - exit_price) * exit_qty
                            partial = trade.to_dict()
                            partial["hit_level"] = f"TP{i+1}"; partial["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                            partial["exit_price"] = exit_price; partial["quantity"] = exit_qty; partial["pnl"] = round(pnl,4)
                            results.append(partial); update_portfolio({'pnl': pnl})
                            remaining_qty -= exit_qty; highest_tp_idx = i
                            trade["highest_tp"] = highest_tp_idx; trade["quantity"] = remaining_qty
                            if i == 0: trade["breakeven"] = True
                            tp_emoji = "🎯"
                            if i == 0: msg = f"{tp_emoji} **TP1 Hit!** 30% closed. SL moved to Breakeven. 🛡️"
                            elif i == 1: msg = f"{tp_emoji} **TP2 Hit!** 10% closed. SL moved to TP1 (1R locked). 🔒"
                            elif i == 2: msg = f"{tp_emoji} **TP3 Hit!** 10% closed. SL moved to TP2 (2R locked). 🔒"
                            elif i == 3: msg = f"{tp_emoji} **TP4 Hit!** 10% closed. SL moved to TP3 (3R locked). 🔒"
                            elif i == 4: msg = f"{tp_emoji} **TP5 Hit!** Final 40% closed – Home run! 🏆💰"
                            alert_line = f"**{sym} {direction}**\n{msg}\nP&L: {pnl:.2f} USDT | Remaining: {remaining_qty:.6f} units"
                            alerts.append(alert_line); print("ALERT:", alert_line)
                            send_discord_message(alert_line)
                            if remaining_qty <= 0: trade_closed = True; break
                    if remaining_qty <= 0: break
                    current_stop = get_current_stop(trade)
                if remaining_qty > 0:
                    sl_hit = (low <= current_stop) if direction=="LONG" else (high >= current_stop)
                    if sl_hit:
                        exit_price = current_stop
                        pnl = (exit_price - entry) * remaining_qty if direction=="LONG" else (entry - exit_price) * remaining_qty
                        final = trade.to_dict()
                        is_be_stop = str(trade.get("breakeven", "False")).upper() == "TRUE"
                        if is_be_stop and highest_tp_idx >= 0: desc = "BREAKEVEN STOP"; pnl = 0.0
                        else: desc = "STOP LOSS" if highest_tp_idx==-1 else f"STOP LOSS after TP{highest_tp_idx+1}"
                        final["hit_level"] = desc; final["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                        final["exit_price"] = exit_price; final["quantity"] = remaining_qty; final["pnl"] = round(pnl,4)
                        results.append(final); update_portfolio({'pnl': pnl}); remaining_qty = 0
                        trade_closed = True
                        alert_line = f"**{sym} {direction}**\n{'🔴' if 'STOP' in desc else '🛑'} {desc}\nP&L: {pnl:.2f} USDT"
                        alerts.append(alert_line); print("ALERT:", alert_line)
                        send_discord_message(alert_line)
                        send_trade_close_chart(trade, desc, exit_price, pnl)
                        break
                if remaining_qty <= 0: break
            if remaining_qty > 0 and not trade_closed:
                trade["quantity"] = remaining_qty; trade["highest_tp"] = highest_tp_idx
                still_open.append(trade)
        except Exception as e: print(f"Error processing trade {trade.get('symbol','?')}: {e}")

    if results: append_csv(TRADE_RESULTS_CSV, pd.DataFrame(results))
    if still_open:
        for t in still_open:
            t["symbol"] = convert_to_binance(t["symbol"])
        save_csv(OPEN_TRADES_CSV, pd.DataFrame(still_open))
        portfolio['open_positions'] = len(still_open)
    else:
        save_csv(OPEN_TRADES_CSV, pd.DataFrame())
        portfolio['open_positions'] = 0
    save_portfolio(portfolio)

    risky_count = sum(1 for t in still_open if str(t.get("breakeven", "False")).upper() != "TRUE")
    be_count = len(still_open) - risky_count
    summary = f"🔍 Open trades status: {risky_count} risky (TP1 not hit yet), {be_count} breakeven (risk-free). Total: {len(still_open)}"
    print(summary); send_discord_message(summary)
    print(f"Trade closures processed: {len(results)}. Still open: {len(still_open)}.")
    if not alerts: print("No trade closures this run.")

def send_trade_close_chart(trade, hit_level, exit_price, pnl):
    sym = trade["symbol"]; entry = float(trade["entry"]); stop = float(trade["stop"])
    tps = [float(trade[f"TP{i+1}"]) for i in range(5)]; direction = trade["action"]
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt; import mplfinance as mpf
        entry_time = datetime.strptime(trade["timestamp"], "%Y-%m-%d %H:%M:%S")
        df = get_binance_klines(sym, '1h', start_time=entry_time, end_time=get_now())
        if df.empty: return
        mpf_style = mpf.make_mpf_style(base_mpf_style='nightclouds', facecolor='#000000', gridcolor='#2a2e39',
                                       rc={'axes.labelcolor':'white','xtick.color':'white','ytick.color':'white','axes.titlecolor':'white'})
        fig, ax = mpf.plot(df, type='candle', style=mpf_style,
                           title=f"{sym} {direction} – {hit_level} (PnL: {pnl:.2f}$)", ylabel='Price',
                           returnfig=True, figsize=(8,6))
        ax.axhline(y=entry, color='#f1c40f', linestyle='--', linewidth=1.5, label='Entry')
        ax.axhline(y=stop, color='#e74c3c', linestyle='--', linewidth=1.5, label='Stop')
        for i, tp in enumerate(tps):
            ax.axhline(y=tp, color='#2ecc71', linestyle='--', linewidth=1, alpha=0.6, label=f'TP{i+1}' if i==0 else None)
        ax.axhline(y=exit_price, color='#e67e22', linewidth=2, label=f'Exit ({hit_level})')
        ax.legend(loc='upper left', facecolor='#000000', edgecolor='white', labelcolor='white')
        chart_path = f"{sym}_close_{get_now().strftime('%Y%m%d_%H%M%S')}.png"
        fig.savefig(chart_path, dpi=100, bbox_inches='tight', facecolor='black')
        plt.close(fig)
        send_discord_image(chart_path, caption=f"{sym} {direction} – {hit_level}")
        os.remove(chart_path)
    except Exception as e: print(f"Close chart error: {e}")

# ========== COMPACT SIGNAL FORMATTING ==========
def format_signal(sig):
    sym = sig["symbol"]; direction = sig["action"]
    entry = sig["limit_price"]; stop = sig["stop_loss"]; tps = sig["take_profits"]
    risk = abs(entry - stop); stop_pct = risk / entry * 100
    direction_icon = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
    tp_str = " / ".join([f"{tp:.2f}" for tp in tps])
    return f"${sym.replace('USDT','')} – {direction_icon} Setup (4H)\nEntry: {entry:.2f} | Stop: {stop:.2f} (-{stop_pct:.2f}%)\nTPs: {tp_str}"

# ========== HOLD MESSAGE ==========
def format_hold_message(top5, top_layers):
    if not top5: return "HOLD – No valid trade setups found. Market is fully trendless."
    lines = [f"HOLD – No high‑conviction crypto setup found.\n📊 **Top Coin Scores** (of {len(top5)})"]
    for idx, (pair, score, direction, _, _, _, _) in enumerate(top5, 1):
        short = pair.replace("USDT","")
        lines.append(f"{idx}. {short} → {direction} ({score:.1f}/13.5)")
    if top_layers:
        top_pair = top5[0][0].replace("USDT",""); top_score = top5[0][1]; top_dir = top5[0][2]
        lines.append(f"\n🔎 **Top Coin Layer Breakdown:** {top_pair} ({top_dir}, {top_score:.1f})")
        for name, (earned, max_, status) in top_layers.items():
            if "FAIL" in status: lines.append(f"• {name} ({max_}): ⚠️ {status}")
            else: lines.append(f"• {name} ({max_}): {'✅' if earned > 0 else '❌'}")
    else: lines.append("\nNo layer data available.")
    lines.append("\n💬 Are you stalking any setups? Drop your watchlist below! 👇")
    return "\n".join(lines)

# ========== CHART ON SIGNAL ==========
def send_trade_chart(signal):
    sym = signal['symbol']
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; import mplfinance as mpf
        df = get_binance_klines(sym, '4h', limit=100)
        if df.empty or len(df) < 20: raise ValueError(f"Only {len(df)} 4h candles")
        mpf_style = mpf.make_mpf_style(base_mpf_style='nightclouds', facecolor='#000000', gridcolor='#2a2e39',
                                       rc={'axes.labelcolor':'white','xtick.color':'white','ytick.color':'white','axes.titlecolor':'white'})
        ema50 = df['Close'].ewm(span=min(50,len(df)), adjust=False).mean()
        addplots = [mpf.make_addplot(ema50, color='#f39c12', width=1.5, label='EMA50')]
        if df['Volume'].sum() > 0:
            typical = (df['High'] + df['Low'] + df['Close']) / 3
            vwap = (typical * df['Volume']).cumsum() / df['Volume'].cumsum()
            addplots.append(mpf.make_addplot(vwap, color='#3498db', width=1, linestyle='--', label='VWAP'))
        fig, axes = mpf.plot(df, type='candle', style=mpf_style, title=f"{sym} 4h", ylabel='Price', addplot=addplots, returnfig=True, figsize=(8,6))
        ax = axes[0]
        entry = signal.get('limit_price'); stop = signal.get('stop_loss'); tps = signal.get('take_profits')
        if entry:
            ax.axhline(y=entry, color='#f1c40f', linestyle='--', linewidth=1.5, label='Entry')
            ax.axhline(y=stop, color='#e74c3c', linestyle='--', linewidth=1.5, label='Stop')
            if tps:
                for i, tp in enumerate(tps):
                    ax.axhline(y=tp, color='#2ecc71', linestyle='--', linewidth=1, alpha=0.8, label=f'TP{i+1}' if i==0 else None)
            ax.legend(loc='upper left', facecolor='#000000', edgecolor='white', labelcolor='white')
        chart_path = f"{sym}_chart.png"
        fig.savefig(chart_path, dpi=100, bbox_inches='tight', facecolor='black')
        plt.close(fig)
        send_discord_image(chart_path, caption=f"{sym} – {signal['action']} Setup (4H)")
        os.remove(chart_path)
        send_discord_message(format_signal(signal))
    except Exception as e:
        print(f"Chart error: {e}")
        send_discord_message(format_signal(signal))

# ========== MAIN ==========
def main():
    try:
        initialize_trade_files()
        check_open_trades()
        try:
            open_df = pd.read_csv(OPEN_TRADES_CSV)
            if not open_df.empty:
                open_df["symbol"] = open_df["symbol"].apply(convert_to_binance)
            print(f"Currently {len(open_df)} open trade(s).")
        except: print("No open trades file.")
        if daily_pnl() <= portfolio['daily_loss_limit']:
            send_discord_message("Daily loss limit reached. No new trades today.")
            return
        sig, top5, top_layers = generate_signal()
        if sig:
            log_signal(sig); add_open_trade(sig)
            portfolio['open_positions'] += 1; save_portfolio(portfolio)
            send_trade_chart(sig)
        else:
            send_discord_message(format_hold_message(top5, top_layers))
    except Exception as e:
        err = f"Bot crashed: {traceback.format_exc()[:500]}"
        print(err); send_discord_message(err)

if __name__ == "__main__":
    main()
