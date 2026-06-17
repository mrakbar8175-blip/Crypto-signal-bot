import requests, json, os, traceback, re, time, random
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

# ========== CSV FILES ==========
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
                             "TP1", "conviction", "ai_confidence"])
    init_csv(OPEN_TRADES_CSV, ["timestamp", "symbol", "action", "entry", "stop",
                               "TP1", "status", "quantity", "original_qty", "ema_trail"])
    init_csv(TRADE_RESULTS_CSV, ["timestamp", "symbol", "action", "entry", "stop",
                                 "TP1", "status", "hit_level",
                                 "close_time", "exit_price", "quantity", "pnl_usdt"])

def log_signal(signal):
    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": signal["symbol"],
        "action": signal["action"],
        "entry": signal["limit_price"],
        "stop": signal["stop_loss"],
        "TP1": signal["take_profits"][0],
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
        "status": "open",
        "quantity": signal["quantity"],
        "original_qty": signal["quantity"],
        "ema_trail": ""
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

# ========== MACRO & TECHNICALS ==========
def institutional_macro_filter():
    df_btc = get_yahoo_klines("BTCUSDT", interval='4h', days=14)
    if df_btc.empty or len(df_btc) < 50: return 0
    closes = df_btc['Close']
    ema50 = closes.ewm(span=50, adjust=False).mean()
    current = closes.iloc[-1]
    btc_bullish = current > ema50.iloc[-1]
    try:
        data = fetch_coingecko("https://api.coingecko.com/api/v3/global")
        usdt_dom = data['data'].get('market_cap_percentage', {}).get('usdt', 5) if data and 'data' in data else 5
    except:
        usdt_dom = 5
    macro = 0
    if btc_bullish: macro += 1
    if usdt_dom < 4.5: macro += 1
    if not btc_bullish: macro -= 1
    if usdt_dom > 6.5: macro -= 1
    return max(-2, min(2, macro))

def anchored_vwap_score(df, current_price):
    if len(df) < 50: return 0
    typical = (df['High'] + df['Low'] + df['Close']) / 3
    df['vpv'] = typical * df['Volume']
    total_vol = df['Volume'].sum()
    if total_vol == 0: return 0
    vwap = df['vpv'].sum() / total_vol
    dev = (current_price - vwap) / vwap * 100
    return 1 if dev > 1 else (-1 if dev < -1 else 0)

def refined_buying_pressure(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=10)
    if df.empty or len(df) < 48: return 0,0
    short = df.tail(12)
    s_buy = short.loc[short['Close'] > short['Open'], 'Volume'].sum()
    s_sell = short.loc[short['Close'] <= short['Open'], 'Volume'].sum()
    s_tot = s_buy + s_sell
    sp = (s_buy - s_sell)/s_tot if s_tot > 0 else 0
    long = df.tail(48)
    l_buy = long.loc[long['Close'] > long['Open'], 'Volume'].sum()
    l_sell = long.loc[long['Close'] <= long['Open'], 'Volume'].sum()
    l_tot = l_buy + l_sell
    lp = (l_buy - l_sell)/l_tot if l_tot > 0 else 0
    return sp, lp

def get_technicals(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=14)
    if df.empty or len(df) < 50:
        return {"combined":0, "trend_dir":"up", "error":"insufficient data"}
    closes = df['Close']
    highs, lows = df['High'], df['Low']
    ema50 = closes.ewm(span=50, adjust=False).mean()
    ema200 = closes.ewm(span=200, adjust=False).mean() if len(closes) >= 200 else ema50
    current = closes.iloc[-1]
    trend = 1.5 if current > ema50.iloc[-1] else -1.5
    trend += 1.5 if ema50.iloc[-1] > ema200.iloc[-1] else -1.5
    trend = max(-3, min(3, trend))
    def adx(high, low, close, period=14):
        dm_plus = high.diff(); dm_minus = -low.diff()
        dm_plus[dm_plus<0]=0; dm_minus[dm_minus<0]=0
        tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1/period, adjust=False).mean()
        di_plus = 100 * (dm_plus.ewm(alpha=1/period, adjust=False).mean() / atr)
        di_minus = 100 * (dm_minus.ewm(alpha=1/period, adjust=False).mean() / atr)
        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
        return dx.ewm(alpha=1/period, adjust=False).mean(), di_plus, di_minus
    adx_s, di_p, di_m = adx(highs, lows, closes)
    adx_now = adx_s.iloc[-1]
    adx_score = 2.5 if adx_now>25 and di_p.iloc[-1]>di_m.iloc[-1] else (-2.5 if adx_now>25 else (1.0 if adx_now>20 and di_p.iloc[-1]>di_m.iloc[-1] else (-1.0 if adx_now>20 else 0)))
    window=7; lookback=min(50,len(highs))
    rh=highs.iloc[-lookback:]; rl=lows.iloc[-lookback:]
    sh, sl = [], []
    for i in range(window,len(rh)-window):
        if all(rh.iloc[i] >= rh.iloc[i-window:i+window+1]): sh.append((i,rh.iloc[i]))
        if all(rl.iloc[i] <= rl.iloc[i-window:i+window+1]): sl.append((i,rl.iloc[i]))
    structure=0
    if len(sh)>=2 and len(sl)>=2:
        last_hh = sh[-1][1] > sh[-2][1]; last_hl = sl[-1][1] > sl[-2][1]
        if last_hh and last_hl:
            structure = 3.0 if (len(sh)>=3 and sh[-2][1]>sh[-3][1] and len(sl)>=3 and sl[-2][1]>sl[-3][1]) else 2.0
        elif not last_hh and not last_hl:
            structure = -3.0 if (len(sh)>=3 and sh[-2][1]<sh[-3][1] and len(sl)>=3 and sl[-2][1]<sl[-3][1]) else -2.0
    structure = max(-3, min(3, structure))
    combined = trend*0.30 + adx_score*0.25 + structure*0.45
    trend_dir = "up" if current > ema50.iloc[-1] else "down"
    return {"combined":combined, "trend_dir":trend_dir, "adx":adx_now, "error":None}

def get_4h_atr(symbol_usdt, current_price):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=14)
    if df.empty or len(df) < 14: return current_price*0.02
    high,low,close = df['High'],df['Low'],df['Close']
    tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    return atr if not pd.isna(atr) else current_price*0.02

def get_buying_pressure(symbol_usdt):
    sp,lp = refined_buying_pressure(symbol_usdt)
    if sp*lp>0: return (sp+lp)/2*3, None
    else: return (sp+lp)/2*3*0.3, None

def get_volatility_score(symbol_usdt, price):
    atr = get_4h_atr(symbol_usdt, price)
    atr_pct = atr/price*100
    return -1 if atr_pct<2 or atr_pct>7 else 1

def btc_trend_score():
    df = get_yahoo_klines("BTCUSDT", interval='4h', days=14)
    if df.empty or len(df)<50: return 0,"BTC unavailable"
    closes = df['Close']; ema50 = closes.ewm(span=50, adjust=False).mean()
    cur = closes.iloc[-1]; ema_now = ema50.iloc[-1]
    slope_up = ema_now > ema50.iloc[-7] if len(ema50)>=7 else True
    price_above = cur > ema_now
    if price_above and slope_up: return 2,None
    elif not price_above and not slope_up: return -2,None
    else: return 0,None

def volume_trend_score(symbol_usdt, direction=None):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=5)
    if df.empty or len(df)<12: return 0,"vol data insufficient"
    recent = df['Volume'].tail(6)
    first, second = recent[:3].mean(), recent[3:].mean()
    if second > first*1.05: return -2 if direction=="down" else 2
    elif second < first*0.95: return -2 if direction=="up" else -2
    return 0,None

def momentum_alignment_score(symbol_usdt, direction, layers):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=2)
    if df.empty or len(df)<2: return 0
    last = df.iloc[-1]
    agrees = (direction=="LONG" and last['Close']>last['Open']) or (direction=="SHORT" and last['Close']<last['Open'])
    if not agrees: return 0
    support=0
    if direction=="LONG":
        if layers.get("buying_press",0)>0.5: support+=1
        if layers.get("intermarket",0)>0.5: support+=1
        if layers.get("volume_trend",0)>0.5: support+=1
    else:
        if layers.get("buying_press",0)<-0.5: support+=1
        if layers.get("intermarket",0)<-0.5: support+=1
        if layers.get("volume_trend",0)<-0.5: support+=1
    return 0.20 if support>=2 and direction=="LONG" else (-0.20 if support>=2 else 0)

def trend_strength_bonus(adx, base):
    if adx>35 and abs(base)>0.5: return 0.30*(1 if base>0 else -1)
    elif adx>30 and abs(base)>0.5: return 0.20*(1 if base>0 else -1)
    return 0

# ========== NEW LAYERS ==========
def candle_strength_score(symbol_usdt, direction, atr):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=2)
    if df.empty or len(df) < 1: return 0
    last = df.iloc[-1]
    candle_range = last['High'] - last['Low']
    if candle_range < 0.5*atr: return 0
    if direction == "LONG" and last['Close'] > last['Open']: return 0.10
    if direction == "SHORT" and last['Close'] < last['Open']: return 0.10
    return 0

def volume_spike_score(symbol_usdt, direction):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=5)
    if df.empty or len(df) < 20: return 0
    avg_vol = df['Volume'].tail(20).mean()
    last_vol = df['Volume'].iloc[-1]
    if last_vol < 1.5*avg_vol: return 0
    last = df.iloc[-1]
    if direction == "LONG" and last['Close'] > last['Open']: return 0.10
    if direction == "SHORT" and last['Close'] < last['Open']: return 0.10
    return 0

def level_proximity_score(symbol_usdt, price, atr, direction):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=14)
    if df.empty or len(df) < 50: return 0
    highs = df['High']; lows = df['Low']
    window=7; lookback=min(50,len(highs))
    rh=highs.iloc[-lookback:]; rl=lows.iloc[-lookback:]
    sh, sl = [], []
    for i in range(window, len(rh)-window):
        if all(rh.iloc[i] >= rh.iloc[i-window:i+window+1]): sh.append(rh.iloc[i])
        if all(rl.iloc[i] <= rl.iloc[i-window:i+window+1]): sl.append(rl.iloc[i])
    if not sh or not sl: return 0
    nearest_high = max(sh) if sh else price
    nearest_low = min(sl) if sl else price
    if direction == "LONG":
        if price - nearest_low < atr: return 0.10
    else:
        if nearest_high - price < atr: return 0.10
    return 0

# ========== SCORING (with new layers) ==========
def score_coin(symbol, price, volume, btc_score, btc_err, macro_score):
    errors = []
    tech = get_technicals(symbol)
    tech_combined = tech["combined"]; trend_dir = tech["trend_dir"]; adx = tech.get("adx",0)
    buying_score, _ = get_buying_pressure(symbol)
    vol_score = get_volatility_score(symbol, price)
    intermarket = btc_score
    vol_trend_s, _ = volume_trend_score(symbol, trend_dir)
    df_vwap = get_yahoo_klines(symbol, interval='4h', days=14)
    vwap_score = anchored_vwap_score(df_vwap, price)
    atr_val = get_4h_atr(symbol, price)
    total = 0.20*tech_combined + 0.45*buying_score + 0.05*vol_score + 0.25*intermarket + 0.05*vol_trend_s
    total *= (1 + 0.15*macro_score)
    total += vwap_score*0.1
    direction = "LONG" if total >= 0 else "SHORT"
    total += candle_strength_score(symbol, direction, atr_val)
    total += volume_spike_score(symbol, direction)
    total += level_proximity_score(symbol, price, atr_val, direction)
    layers = {"tech":tech_combined,"buying_press":buying_score,"volatility":vol_score,"intermarket":intermarket,"volume_trend":vol_trend_s}
    return max(-3, min(3, total)), layers, trend_dir, adx

def compute_confidence(layers):
    scores = [layers[k] for k in ["tech","buying_press","intermarket","volume_trend"]]
    bear = sum(1 for s in scores if s<-0.5); bull = sum(1 for s in scores if s>0.5)
    aligned = max(bear,bull)
    if aligned>=4: return 7
    if aligned>=3: return 6
    if aligned>=2: return 5
    return 4

# ========== QWEN POST (MORE VARIETY) ==========
def generate_post(coin, direction, btc_score, macro_score):
    sym = coin["symbol"]; ticker = sym.replace("USDT","")
    price = coin["price"]; atr = coin["atr"]; layers = coin["layers"]
    tech = get_technicals(sym); trend_dir = tech["trend_dir"]
    ema_rel = "above" if trend_dir=="up" else "below"
    ema_slope = "rising" if trend_dir=="up" else "falling"
    vwap_score = anchored_vwap_score(get_yahoo_klines(sym, interval='4h', days=14), price)
    vwap_rel = "above" if vwap_score>0 else "below" if vwap_score<0 else "near"
    vol_trend = "increasing" if layers["volume_trend"]>0 else "decreasing" if layers["volume_trend"]<0 else "flat"
    btc_bullish = btc_score>0
    btc_text = "bullish" if btc_bullish else "bearish"

    # Stronger anti-template prompt
    prompt = (
        f"You are a professional Crypto Analyst writing a unique Binance Square post for ${ticker} ({direction} setup). "
        f"The 4‑hour chart shows: price is {ema_rel} the 50‑EMA and the EMA is {ema_slope}. "
        f"Anchored VWAP is {vwap_rel} price. Volume is {vol_trend}. $BTC is {btc_text} on its 4‑hour chart. "
        "CRITICAL INSTRUCTIONS: Never start with the same phrase twice. Vary your vocabulary, sentence structure, and hook completely. "
        "Write the 4 sections: 1. Compliant Hook, 2. Humanised Chart Reading (explain the EMA, VWAP, volume, BTC context), "
        "3. Risk‑Managed Levels (use the placeholder lines), 4. CTA & Footer with hashtags. "
        "Do not include any RATING prefix. Output only the final post."
    )

    def call_qwen(p, t=0.95):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "qwen-2.5-72b",
            "messages": [{"role": "user", "content": p}],
            "temperature": t,
            "max_tokens": 500
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except:
            pass
        return None

    text = call_qwen(prompt, 0.95)
    if not text or len(text) < 200:
        text = call_qwen(prompt, 1.0)

    if not text:
        # fallback that's slightly varied
        if direction == "LONG":
            hook = f"${ticker} is showing renewed strength as buyers step in near a key zone."
        else:
            hook = f"Sellers are keeping pressure on ${ticker} as it struggles below the 50‑EMA."
        text = (
            f"{hook}\n\n"
            f"The 50‑EMA is {ema_slope} and price is hovering {ema_rel} it, while the anchored VWAP is acting as a {vwap_rel} reference. "
            f"Volume has been {vol_trend}, suggesting { 'conviction' if vol_trend=='increasing' else 'a lack of momentum' }. "
            f"$BTC is {btc_text}, which typically {'provides a tailwind' if btc_bullish else 'adds caution'} for altcoins like ${ticker}.\n\n"
            f"🟢 {direction} Setup Structure:\n"
            f"• Area of Interest: {price:.6f}\n"
            f"• Technical Invalidation: [insert]\n"
            f"• Target Objectives: [insert]\n\n"
            f"What’s your take on ${ticker} right now? Let me know in the comments!\n"
            f"#CryptoAnalysis #{ticker} #TechnicalAnalysis #BinanceSquare\n"
            f"*Disclaimer: This analysis is based on technical indicators for educational and informational purposes only. "
            f"This is not financial advice. Always practice strict risk management and do your own research (DYOR).*"
        )

    # remove any accidental RATING prefix
    text = re.sub(r'^RATING:\s*\d+\s*\|?\s*', '', text).strip()
    return text

# ========== TRADE MANAGER (50% at TP1 0.5R, 34‑EMA trail) ==========
def check_open_trades():
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
    except: return
    if open_df.empty: return
    if "timestamp" in open_df.columns: open_df = open_df.sort_values("timestamp").drop_duplicates("symbol", keep="last")
    else: open_df = open_df.drop_duplicates("symbol", keep="last")
    results = []; still_open = []; alerts = []
    now = datetime.now()
    for _, trade in open_df.iterrows():
        sym = trade["symbol"]; direction = trade["action"]
        entry = float(trade["entry"]); stop_orig = float(trade["stop"])
        qty = float(trade["quantity"]); orig_qty = float(trade.get("original_qty", qty))
        tp1 = float(trade["TP1"])
        tp1_hit = trade.get("ema_trail", "") != "" or float(trade.get("quantity",0)) < orig_qty
        remaining = qty
        df_1h = get_yahoo_klines(sym, interval='1h', start=datetime.strptime(trade["timestamp"],"%Y-%m-%d %H:%M:%S"), end=now)
        if df_1h.empty: still_open.append(trade); continue
        if tp1_hit:
            current_stop = entry
            df_4h = get_yahoo_klines(sym, interval='4h', days=5)
            if not df_4h.empty:
                ema34 = df_4h['Close'].ewm(span=34, adjust=False).mean().iloc[-1]
                atr_val = get_4h_atr(sym, entry)
                buffer = 0.5 * atr_val
                if direction == "LONG": current_stop = max(current_stop, ema34 - buffer)
                else: current_stop = min(current_stop, ema34 + buffer)
        else:
            current_stop = stop_orig
        outcome = None; exit_price = None
        for _, candle in df_1h.iterrows():
            high, low = candle['High'], candle['Low']
            if not tp1_hit:
                if (direction == "LONG" and high >= tp1) or (direction == "SHORT" and low <= tp1):
                    exit_qty = orig_qty * 0.50
                    pnl = (tp1 - entry) * exit_qty if direction=="LONG" else (entry - tp1) * exit_qty
                    partial = trade.to_dict(); partial["hit_level"] = "TP1 (50%)"
                    partial["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S"); partial["exit_price"] = tp1
                    partial["quantity"] = exit_qty; partial["pnl_usdt"] = round(pnl,4)
                    results.append(partial); update_portfolio({'pnl_usdt': pnl})
                    remaining -= exit_qty; tp1_hit = True; current_stop = entry
                    alerts.append(f"🚀 {sym.replace('USDT','')} {direction} TP1 hit — 50% closed, SL now BE")
            if remaining > 0:
                sl_hit = (low <= current_stop) if direction=="LONG" else (high >= current_stop)
                if sl_hit:
                    exit_qty = remaining; exit_price = current_stop
                    pnl = (exit_price - entry) * exit_qty if direction=="LONG" else (entry - exit_price) * exit_qty
                    final = trade.to_dict(); desc = "STOP LOSS" if not tp1_hit else "TRAILING STOP"
                    final["hit_level"] = desc; final["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                    final["exit_price"] = exit_price; final["quantity"] = exit_qty; final["pnl_usdt"] = round(pnl,4)
                    results.append(final); update_portfolio({'pnl_usdt': pnl})
                    remaining = 0; alerts.append(f"🔴 {sym.replace('USDT','')} {direction} → {desc} (remaining closed)")
                    break
        if remaining > 0:
            trade["quantity"] = remaining
            trade["ema_trail"] = "active" if tp1_hit else ""
            still_open.append(trade)
    if results:
        dfr = pd.DataFrame(results); append_csv(TRADE_RESULTS_CSV, dfr)
    if still_open:
        dfs = pd.DataFrame(still_open); save_csv(OPEN_TRADES_CSV, dfs); portfolio['open_positions'] = len(dfs)
    else:
        save_csv(OPEN_TRADES_CSV, pd.DataFrame()); portfolio['open_positions'] = 0
    save_portfolio(portfolio)
    if alerts: send_telegram("\n".join(alerts))

# ========== SIGNAL GENERATION ==========
def generate_signal(balance_usdt):
    coins_data = fetch_coingecko("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1")
    if not coins_data: return {"action":"HOLD"}
    open_symbols = set()
    risky = 0
    try:
        odf = pd.read_csv(OPEN_TRADES_CSV)
        if not odf.empty:
            odf = odf.sort_values("timestamp").drop_duplicates("symbol",keep="last") if "timestamp" in odf.columns else odf.drop_duplicates("symbol",keep="last")
            open_symbols = set(odf["symbol"])
            risky = sum((odf["ema_trail"]=="") & (odf["quantity"]>0)) if "ema_trail" in odf.columns else len(odf)
    except: pass
    if risky >= 3: return {"action":"HOLD","reasoning":"Max 3 risky trades."}
    candidates = []
    for c in coins_data:
        sym = c.get("symbol","").upper()+"USDT"
        price = c.get("current_price")
        if price and price>0 and sym not in open_symbols:
            candidates.append({"symbol":sym,"price":price,"volume":c.get("total_volume",0)})
    # FIX: sort in-place then slice
    candidates.sort(key=lambda x: x["volume"], reverse=True)
    candidates = candidates[:50]
    if not candidates: return {"action":"HOLD"}
    btc_score, btc_err = btc_trend_score()
    macro = institutional_macro_filter()
    all_scored = []
    for coin in candidates:
        total, layers, trend_dir, adx = score_coin(coin["symbol"], coin["price"], coin["volume"], btc_score, btc_err, macro)
        atr = get_4h_atr(coin["symbol"], coin["price"])
        if atr/coin["price"] > 0.07: total = 0
        coin["score"] = total; coin["atr"] = atr; coin["layers"] = layers; coin["trend_dir"] = trend_dir; coin["adx"] = adx
        all_scored.append(coin)
    best = max(all_scored, key=lambda x: abs(x["score"]))
    if abs(best["score"]) < 1.49: return {"action":"HOLD","reasoning":f"Best score {best['score']:.2f}"}
    direction = "LONG" if best["score"]>=0 else "SHORT"
    entry = best.get("bid", best["price"]*0.999) if direction=="LONG" else best.get("ask", best["price"]*1.001)
    atr = best["atr"]
    df_1h_recent = get_yahoo_klines(best["symbol"], interval='1h', days=2)
    confirm = False
    if not df_1h_recent.empty:
        last_candle = df_1h_recent.iloc[-1]
        if direction=="LONG" and last_candle['Close'] > last_candle['Open']: confirm = True
        elif direction=="SHORT" and last_candle['Close'] < last_candle['Open']: confirm = True
    stop_mult = 1.2 if confirm else 1.5
    min_stop = max(stop_mult * atr, entry * 0.01)
    stop = entry - min_stop if direction=="LONG" else entry + min_stop
    risk_per_share = abs(entry - stop)
    qty = round((balance_usdt * 0.01) / risk_per_share, 6)
    tp1 = round(entry + 0.5*risk_per_share, 6) if direction=="LONG" else round(entry - 0.5*risk_per_share, 6)
    post = generate_post(best, direction, btc_score, macro)
    sl_pct = abs(entry - stop)/entry*100
    post = re.sub(r'• Area of Interest: .*', f'• Area of Interest: {entry:.6f}', post)
    post = re.sub(r'• Technical Invalidation: .*', f'• Technical Invalidation: {stop:.6f} ({sl_pct:.2f}%)', post)
    post = re.sub(r'• Target Objectives: .*', f'• Target Objectives: {tp1:.6f} (then trail with 34‑EMA)', post)
    return {
        "action": direction,
        "symbol": best["symbol"],
        "quantity": qty,
        "limit_price": entry,
        "stop_loss": stop,
        "take_profits": [tp1],
        "confidence_score": compute_confidence(best["layers"]),
        "conviction_score": abs(best["score"]),
        "post_text": post
    }

# ========== CHART & TELEGRAM ==========
def send_trade_chart(signal):
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; import mplfinance as mpf
        sym = signal['symbol']; df = get_yahoo_klines(sym, interval='4h', days=10)
        if df.empty: return
        style = mpf.make_mpf_style(base_mpf_style='nightclouds', facecolor='#000000', gridcolor='#2a2e39',
                                   rc={'axes.labelcolor':'white','xtick.color':'white','ytick.color':'white','axes.titlecolor':'white'})
        ema50 = df['Close'].ewm(span=50, adjust=False).mean()
        typical = (df['High']+df['Low']+df['Close'])/3; vwap = (typical*df['Volume']).cumsum()/df['Volume'].cumsum()
        apds = [mpf.make_addplot(ema50,color='#f39c12',width=1.5,label='EMA50'),
                mpf.make_addplot(vwap,color='#3498db',width=1,linestyle='--',label='VWAP')]
        fig,axes=mpf.plot(df,type='candle',style=style,title=f"{sym.replace('USDT','')} 4h",ylabel='Price',addplot=apds,returnfig=True,figsize=(8,6))
        ax=axes[0]
        entry=signal.get('limit_price'); stop=signal.get('stop_loss'); tps=signal.get('take_profits')
        if entry and stop:
            ax.axhline(y=entry,color='#f1c40f',linestyle='--',linewidth=1.5,label='Entry')
            ax.axhline(y=stop,color='#e74c3c',linestyle='--',linewidth=1.5,label='Stop')
            if tps:
                for i,tp in enumerate(tps): ax.axhline(y=tp,color='#2ecc71',linestyle='--',linewidth=1,alpha=0.8,label=f'TP{i+1}')
            ax.legend(loc='upper left',facecolor='#000000',edgecolor='white',labelcolor='white')
        path=f"{sym.replace('USDT','')}_chart.png"
        fig.savefig(path,dpi=150,bbox_inches='tight',facecolor='black'); plt.close(fig)
        url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(path,'rb') as img: requests.post(url,data={'chat_id':CHAT_ID},files={'photo':img})
        os.remove(path)
    except ImportError:
        base=signal['symbol'].replace("USDT","").upper(); studies="&studies[]=STD%3BEMA%3B50&studies[]=STD%3BVWAP"
        send_telegram(f"📈 Chart with EMA & VWAP: https://www.tradingview.com/chart/?symbol=BINANCE:{base}USDT&interval=240{studies}")
    except Exception as e: print(f"Chart error: {e}")

def send_telegram(text):
    url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url,data={"chat_id":CHAT_ID,"text":text},timeout=10)
    except Exception as e: print("TG error:",e)

def main():
    try:
        initialize_trade_files()
        check_open_trades()
        if get_daily_pnl() <= portfolio['daily_loss_limit']:
            send_telegram(f"Daily loss limit reached (PnL: {get_daily_pnl():.2f} USD). No new trades.")
            return
        dec = generate_signal(portfolio['balance_usdt'])
        if dec['action'] in ["LONG","SHORT"]:
            log_signal(dec); add_open_trade(dec); portfolio['open_positions'] += 1; save_portfolio(portfolio)
            send_telegram(dec['post_text'])
            send_trade_chart(dec)
        else:
            send_telegram(f"HOLD\n{dec.get('reasoning','No signal')}")
    except Exception as e:
        err = f"Bot crashed: {traceback.format_exc()}"
        print(err); send_telegram(err[:500])

if __name__ == "__main__":
    main()