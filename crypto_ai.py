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
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=days)
    else:
        start_dt = start
        end_dt = end if end is not None else datetime.now()
    for attempt in range(2):
        try:
            df = yf.download(yahoo_symbol, start=start_dt, end=end_dt, interval=interval, progress=False)
            if not df.empty and len(df) >= 10:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                if {'Open','High','Low','Close','Volume'}.issubset(df.columns):
                    return df
            time.sleep(2)
        except:
            time.sleep(2)
    return pd.DataFrame()

def get_klines_with_fallback(symbol_usdt, interval='4h', days=10):
    """Fetch 4h data; if insufficient, try 1h data (3 days)."""
    df = get_yahoo_klines(symbol_usdt, interval=interval, days=days)
    if not df.empty and len(df) >= 20:
        return df
    # fallback to 1h
    df1 = get_yahoo_klines(symbol_usdt, interval='1h', days=3)
    if not df1.empty and len(df1) >= 20:
        return df1
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
        "TP1": signal["take_profits"][0] if len(signal["take_profits"]) > 0 else "",
        "TP2": signal["take_profits"][1] if len(signal["take_profits"]) > 1 else "",
        "TP3": signal["take_profits"][2] if len(signal["take_profits"]) > 2 else "",
        "TP4": signal["take_profits"][3] if len(signal["take_profits"]) > 3 else "",
        "TP5": signal["take_profits"][4] if len(signal["take_profits"]) > 4 else "",
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

# ========== MACRO BIAS (CoinGecko, never zero) ==========
def get_macro_bias():
    bias = 0.0
    try:
        cg_simple = fetch_coingecko(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
        )
        if cg_simple and 'bitcoin' in cg_simple:
            change = cg_simple['bitcoin'].get('usd_24h_change', 0)
            bias += max(-1.0, min(1.0, change / 5.0)) * 0.6
    except:
        pass
    try:
        cg_daily = fetch_coingecko(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=14"
        )
        if cg_daily and 'prices' in cg_daily:
            prices = cg_daily['prices']
            if len(prices) >= 7:
                daily = [p[1] for p in prices[-7:]]
                ema = daily[0]
                for p in daily[1:]:
                    ema = p * 0.25 + ema * 0.75
                current = daily[-1]
                if current > 0:
                    deviation = (current - ema) / current
                    bias += max(-1.0, min(1.0, deviation * 20)) * 0.4
    except:
        pass
    return max(-1.0, min(1.0, bias))

# ========== LAYERS WITH DATA FALLBACK ==========
def safe_ema(series, span):
    try:
        return series.ewm(span=span, adjust=False).mean()
    except:
        return pd.Series(index=series.index)

def get_4h_atr(symbol_usdt, current_price):
    try:
        df = get_klines_with_fallback(symbol_usdt, interval='4h', days=14)
        if df.empty or len(df) < 14: return current_price * 0.02
        high, low, close = df['High'], df['Low'], df['Close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = safe_ema(tr, 14).iloc[-1]
        return atr if not pd.isna(atr) else current_price * 0.02
    except:
        return current_price * 0.02

def candle_conviction_score(symbol_usdt, direction, atr):
    try:
        df = get_klines_with_fallback(symbol_usdt, interval='4h', days=2)
        if df.empty: return 0
        last = df.iloc[-1]
        body = abs(last['Close'] - last['Open'])
        candle_range = last['High'] - last['Low']
        if candle_range <= 0: return 0
        if direction == "LONG":
            if last['Close'] > last['Open']:
                close_pos = (last['Close'] - last['Low']) / candle_range
                body_ratio = body / candle_range
                return (close_pos * 0.6 + body_ratio * 0.4) * 0.8
            else:
                return -0.3
        else:
            if last['Close'] < last['Open']:
                close_pos = (last['High'] - last['Close']) / candle_range
                body_ratio = body / candle_range
                return (close_pos * 0.6 + body_ratio * 0.4) * 0.8
            else:
                return -0.3
    except:
        return 0

def momentum_score(symbol_usdt, direction, atr):
    try:
        df = get_klines_with_fallback(symbol_usdt, interval='4h', days=2)
        if df.empty: return 0
        last = df.iloc[-1]
        candle_range = last['High'] - last['Low']
        if atr <= 0: return 0
        range_vs_atr = candle_range / atr
        if direction == "LONG":
            if last['Close'] > last['Open']:
                return min(1.0, range_vs_atr * 0.6)
            else:
                return -min(1.0, range_vs_atr * 0.6)
        else:
            if last['Close'] < last['Open']:
                return min(1.0, range_vs_atr * 0.6)
            else:
                return -min(1.0, range_vs_atr * 0.6)
    except:
        return 0

def volume_surge_score(symbol_usdt, direction):
    try:
        df = get_klines_with_fallback(symbol_usdt, interval='4h', days=5)
        if df.empty or len(df) < 21: return 0
        last_vol = df['Volume'].iloc[-1]
        avg_vol = df['Volume'].tail(21).mean()
        if avg_vol <= 0: return 0
        vol_ratio = last_vol / avg_vol
        if vol_ratio > 5.0: vol_ratio = 5.0
        surge = (vol_ratio - 1) / 4.0
        last = df.iloc[-1]
        if direction == "LONG":
            if last['Close'] > last['Open']:
                return max(-1.0, min(1.0, surge))
            else:
                return -max(-1.0, min(1.0, surge))
        else:
            if last['Close'] < last['Open']:
                return max(-1.0, min(1.0, surge))
            else:
                return -max(-1.0, min(1.0, surge))
    except:
        return 0

def ema_trend_score(symbol_usdt, direction):
    try:
        df = get_klines_with_fallback(symbol_usdt, interval='4h', days=10)
        if df.empty or len(df) < 20: return 0
        closes = df['Close']
        ema20 = safe_ema(closes, 20)
        current = closes.iloc[-1]
        ema_now = ema20.iloc[-1]
        if len(ema20) >= 6:
            slope_up = ema_now > ema20.iloc[-6]
        else:
            slope_up = True
        price_above = current > ema_now
        if direction == "LONG":
            score = 0
            if price_above: score += 0.6
            if slope_up: score += 0.4
            return score
        else:
            score = 0
            if not price_above: score += 0.6
            if not slope_up: score += 0.4
            return -score
    except:
        return 0

def rate_of_change_score(symbol_usdt, direction):
    try:
        df = get_klines_with_fallback(symbol_usdt, interval='4h', days=2)
        if df.empty or len(df) < 4: return 0
        closes = df['Close']
        roc = (closes.iloc[-1] / closes.iloc[-4] - 1) * 100
        if direction == "LONG":
            return max(-1.0, min(1.0, roc / 2.0))
        else:
            return -max(-1.0, min(1.0, roc / 2.0))
    except:
        return 0

def relative_strength_score(symbol_usdt, direction, btc_df):
    try:
        df = get_klines_with_fallback(symbol_usdt, interval='4h', days=10)
        if df.empty or len(df) < 6 or btc_df is None or btc_df.empty or len(btc_df) < 6:
            return 0
        coin_perf = (df['Close'].iloc[-1] / df['Close'].iloc[-6] - 1)
        btc_perf = (btc_df['Close'].iloc[-1] / btc_df['Close'].iloc[-6] - 1)
        rs = coin_perf - btc_perf
        if direction == "LONG":
            return max(-1.0, min(1.0, rs * 10))
        else:
            return -max(-1.0, min(1.0, rs * 10))
    except:
        return 0

# ========== SCORING ENGINE ==========
def score_coin(symbol, price, volume, macro_bias, coin_data_cache, btc_df):
    try:
        if symbol in coin_data_cache:
            df = coin_data_cache[symbol]
        else:
            df = get_klines_with_fallback(symbol, interval='4h', days=10)
            coin_data_cache[symbol] = df

        if df.empty or len(df) < 20:
            return 0, {"conviction":0,"momentum":0,"volume":0,"ema_trend":0,"roc":0,"macro_bias":0,"rs":0}, "up", price*0.02

        last_candle = df.iloc[-1]
        if last_candle['Close'] > last_candle['Open']:
            direction = "LONG"
        else:
            direction = "SHORT"

        atr_val = get_4h_atr(symbol, price)

        conv = candle_conviction_score(symbol, direction, atr_val)
        mom = momentum_score(symbol, direction, atr_val)
        vol = volume_surge_score(symbol, direction)
        ema = ema_trend_score(symbol, direction)
        roc = rate_of_change_score(symbol, direction)
        rs = relative_strength_score(symbol, direction, btc_df)

        total = (0.20 * mom + 0.20 * conv + 0.15 * vol + 0.15 * ema + 0.10 * roc +
                 0.10 * macro_bias + 0.10 * rs)

        # Bonus for extreme close (>75% of range)
        candle_range = last_candle['High'] - last_candle['Low']
        if candle_range > 0:
            if direction == "LONG":
                close_pct = (last_candle['Close'] - last_candle['Low']) / candle_range
                if close_pct > 0.75: total += 0.12
            else:
                close_pct = (last_candle['High'] - last_candle['Close']) / candle_range
                if close_pct > 0.75: total += 0.12

        total = max(-3, min(3, total))
        layers = {
            "conviction": conv,
            "momentum": mom,
            "volume": vol,
            "ema_trend": ema,
            "roc": roc,
            "macro_bias": macro_bias,
            "rs": rs
        }
        return total, layers, direction, atr_val
    except Exception as e:
        print(f"score_coin error {symbol}: {e}")
        return 0, {"conviction":0,"momentum":0,"volume":0,"ema_trend":0,"roc":0,"macro_bias":0,"rs":0}, "up", price*0.02

def compute_confidence(layers):
    scores = [layers["momentum"], layers["conviction"], layers["volume"], layers["ema_trend"]]
    bear = sum(1 for s in scores if s < -0.3)
    bull = sum(1 for s in scores if s > 0.3)
    aligned = max(bear, bull)
    if aligned >= 4: return 7
    if aligned >= 3: return 6
    if aligned >= 2: return 5
    return 4

# ========== LLAMA QUALITY FILTER (top 5) ==========
def evaluate_deep(coin, direction, macro_bias):
    sym = coin["symbol"]
    price = coin["price"]
    atr = coin["atr"]
    layers = coin["layers"]

    prompt = (
        f"Symbol: {sym} | Direction: {direction} | Price: {price:.4f} | ATR: {atr:.4f}\n"
        f"Macro Bias (BTC): {macro_bias:.2f} (‑1 bearish, +1 bullish).\n"
        f"Layers: conviction={layers['conviction']:.2f}, momentum={layers['momentum']:.2f}, "
        f"volume={layers['volume']:.2f}, ema_trend={layers['ema_trend']:.2f}, "
        f"roc={layers['roc']:.2f}, rs={layers['rs']:.2f}.\n\n"
        "You are a senior crypto analyst. Rate this setup from 1 to 10, where 10 is a perfect, high‑probability trade "
        "with strong trend alignment, clear momentum, and confirming volume. "
        "Be strict: only give a high rating if the setup is clean and has a clear edge. "
        "If the data provided is insufficient to judge, return a rating of 5 and state that the data is limited. "
        "Do not invent patterns or structures that are not explicitly supported by the given data. "
        "Give a very brief reason for your rating. Output format exactly:\n"
        "RATING: 8 | REASON: [short reason]"
    )

    def call_llama(p, temp=0.3):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": p}],
            "temperature": temp,
            "max_tokens": 150
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=40)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except:
            pass
        return None

    text = call_llama(prompt, 0.3)
    rating = 5
    reason = ""
    if text:
        rat_match = re.search(r'RATING:\s*(\d+)', text)
        if rat_match:
            rating = int(rat_match.group(1))
            rating = max(1, min(10, rating))
        reason_match = re.search(r'REASON:\s*(.*)', text)
        if reason_match:
            reason = reason_match.group(1).strip()
    return rating, reason

# ========== VIRAL POST GENERATOR ==========
def generate_post(coin, direction, entry, stop, tps, sl_pct, qwen_reason=""):
    sym = coin["symbol"]; ticker = sym.replace("USDT","")
    price = coin["price"]; atr = coin["atr"]; layers = coin["layers"]

    recent_candles = ""
    try:
        df = get_klines_with_fallback(sym, interval='4h', days=2)
        if not df.empty:
            last_candles = df.tail(6)
            candle_list = []
            for idx, row in last_candles.iterrows():
                candle_list.append(
                    f"O:{row['Open']:.4f} H:{row['High']:.4f} L:{row['Low']:.4f} C:{row['Close']:.4f} V:{row['Volume']:.0f}"
                )
            recent_candles = "\n".join(candle_list)
    except:
        pass

    tp_str = f"{tps[0]:.6f} (0.5R) / {tps[1]:.6f} (1R) / {tps[2]:.6f} (2R) / {tps[3]:.6f} (3R) / {tps[4]:.6f} (5R)"

    system_msg = (
        "You are a legendary crypto chart analyst with 15 years of experience. "
        "Your posts on Binance Square go viral every time because you combine deep technical knowledge with a natural, "
        "human storytelling style. You identify candlestick patterns, chart formations, and key levels like a pro. "
        "Your hooks are irresistible – they create curiosity and FOMO. "
        "Your analysis is detailed yet easy to read, breaking down trend, momentum, volume, and market context. "
        "You always include exact risk‑managed levels and end with an engaging question. "
        "Use emojis sparingly but effectively to highlight key points. "
        "Never output 'RATING:' or any meta commentary. "
        "IMPORTANT: Mention specific candlestick patterns (e.g., bullish engulfing, doji, hammer, shooting star) "
        "and chart structures (e.g., ascending triangle, double bottom, breakout of resistance) only if they are clearly "
        "visible in the candle data provided. If the data is insufficient, focus on the technical indicators."
    )

    user_prompt = (
        f"Write a Binance Square post for a {direction} setup on ${ticker} (USDT pair, 4‑hour chart).\n\n"
        f"Recent 4‑hour candles (newest first):\n{recent_candles}\n\n"
        f"Technical context:\n"
        f"- Price: {price:.4f}\n"
        f"- Momentum: {'bullish' if layers['momentum']>0 else 'bearish'}. Volume: {'supportive' if layers['volume']>0 else 'weak'}.\n"
        f"- BTC macro bias: {layers['macro_bias']:.2f} (‑1 bearish, +1 bullish).\n"
        f"{'Analyst note: ' + qwen_reason if qwen_reason else ''}\n\n"
        f"Risk‑managed levels (use exactly these numbers):\n"
        f"- Area of Interest: {entry:.6f}\n"
        f"- Technical Invalidation: {stop:.6f} ({sl_pct:.2f}%)\n"
        f"- Target Objectives: {tp_str}\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. Start with a craving, scroll‑stopping hook that includes the cashtag and mentions a pattern or level.\n"
        "2. Write a detailed, multi‑paragraph analysis explaining what the candles are telling you – include candlestick patterns, "
        "support/resistance, and how the EMA/VWAP/volume confirm the bias.\n"
        "3. List the risk levels exactly as provided.\n"
        "4. Ask an open‑ended question that invites discussion.\n"
        "5. End with: #CryptoAnalysis #{ticker} #TechnicalAnalysis #BinanceSquare\n"
        "and the standard disclaimer."
    )

    def call_llama(sys_msg, usr_msg, temp=0.9):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": usr_msg}
            ],
            "temperature": temp,
            "max_tokens": 800
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except:
            pass
        return None

    text = call_llama(system_msg, user_prompt, 0.9)
    if not text or len(text) < 200:
        text = call_llama(system_msg, user_prompt, 1.0)

    if not text:
        hooks = [
            f"I've been watching ${ticker} closely – the technicals just lined up in a way I can't ignore. ⚡",
            f"${ticker} is printing a textbook {direction.lower()} structure on the 4‑hour chart. 🚀",
            f"Most traders are sleeping on ${ticker} right now, but the EMA20 just gave a clear signal. 🧐",
        ]
        hook = random.choice(hooks)
        direction_icon = "📈" if direction=="LONG" else "📉"
        text = (
            f"{hook} {direction_icon}\n\n"
            f"Momentum is {'bullish' if layers['momentum']>0 else 'bearish'}, and volume {'confirms' if layers['volume']>0 else 'is neutral'}. "
            f"The macro backdrop is {'supportive' if layers['macro_bias']>0 else 'cautious'} for altcoins.\n\n"
            f"🎯 Risk‑Managed Levels:\n"
            f"• Area of Interest: {entry:.6f}\n"
            f"• Technical Invalidation: {stop:.6f} ({sl_pct:.2f}%)\n"
            f"• Target Objectives: {tp_str}\n\n"
            f"What’s your game plan for ${ticker}? Are you entering now or waiting for a retest?\n"
            f"#CryptoAnalysis #{ticker} #TechnicalAnalysis #BinanceSquare\n"
            f"*Disclaimer: This analysis is for educational purposes only and does not constitute financial advice. Always DYOR.*"
        )
    return text.strip()

# ========== TRADE MANAGER ==========
def check_open_trades():
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
    except: return
    if open_df.empty: return
    if "timestamp" in open_df.columns: open_df = open_df.sort_values("timestamp").drop_duplicates("symbol", keep="last")
    else: open_df = open_df.drop_duplicates("symbol", keep="last")
    results = []; still_open = []; alerts = []
    now = datetime.now()
    fractions = [0.20, 0.20, 0.20, 0.20, 0.20]
    for _, trade in open_df.iterrows():
        try:
            sym = trade["symbol"]; direction = trade["action"]
            entry = float(trade["entry"]); stop_orig = float(trade["stop"])
            orig_qty = float(trade.get("original_qty", trade["quantity"]))
            remaining_qty = float(trade.get("quantity", orig_qty))
            highest_tp_idx = int(trade.get("highest_tp", -1))
            tps = []
            for i in range(1,6):
                tps.append(float(trade[f"TP{i}"]))
            df_1h = get_yahoo_klines(sym, interval='1h', start=datetime.strptime(trade["timestamp"],"%Y-%m-%d %H:%M:%S"), end=now)
            if df_1h.empty: still_open.append(trade); continue
            current_stop = entry if highest_tp_idx >= 0 else stop_orig
            full_close_data = None
            for _, candle in df_1h.iterrows():
                high, low = candle['High'], candle['Low']
                new_tp_idx = None
                if direction == "LONG":
                    for i in range(len(tps)-1, -1, -1):
                        if high >= tps[i] and i > highest_tp_idx:
                            new_tp_idx = i; break
                else:
                    for i in range(len(tps)-1, -1, -1):
                        if low <= tps[i] and i > highest_tp_idx:
                            new_tp_idx = i; break
                if new_tp_idx is not None:
                    for i in range(highest_tp_idx+1, new_tp_idx+1):
                        if remaining_qty <= 0: break
                        if i == 0:
                            current_stop = entry
                        fraction = fractions[i]
                        exit_qty = orig_qty * fraction
                        if exit_qty > remaining_qty: exit_qty = remaining_qty
                        if exit_qty > 0:
                            exit_price = tps[i]
                            pnl = (exit_price - entry) * exit_qty if direction=="LONG" else (entry - exit_price) * exit_qty
                            partial = trade.to_dict()
                            partial["hit_level"] = f"TP{i+1} (partial)"
                            partial["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                            partial["exit_price"] = exit_price
                            partial["quantity"] = exit_qty
                            partial["pnl_usdt"] = round(pnl,4)
                            results.append(partial)
                            update_portfolio({'pnl_usdt': pnl})
                            remaining_qty -= exit_qty
                            highest_tp_idx = i
                            alerts.append(f"🚀 {sym.replace('USDT','')} {direction} TP{i+1} hit — {fraction*100:.0f}% closed")
                        if i == 4:
                            if remaining_qty > 0:
                                final_exit_qty = remaining_qty
                                exit_price = tps[4]
                                pnl = (exit_price - entry) * final_exit_qty if direction=="LONG" else (entry - exit_price) * final_exit_qty
                                final = trade.to_dict()
                                final["hit_level"] = "TP5 (final)"
                                final["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                                final["exit_price"] = exit_price
                                final["quantity"] = final_exit_qty
                                final["pnl_usdt"] = round(pnl,4)
                                results.append(final)
                                update_portfolio({'pnl_usdt': pnl})
                                remaining_qty = 0
                                highest_tp_idx = 4
                                full_close_data = {
                                    "symbol": sym, "action": direction,
                                    "limit_price": entry, "stop_loss": stop_orig,
                                    "take_profits": tps
                                }
                                alerts.append(f"🔔 {sym.replace('USDT','')} {direction} TP5 hit — remaining closed")
                            break
                    if remaining_qty <= 0 and i == 4:
                        break
                if remaining_qty > 0:
                    sl_hit = (low <= current_stop) if direction=="LONG" else (high >= current_stop)
                    if sl_hit:
                        exit_qty = remaining_qty; exit_price = current_stop
                        pnl = (exit_price - entry) * exit_qty if direction=="LONG" else (entry - exit_price) * exit_qty
                        final = trade.to_dict()
                        desc = "STOP LOSS" if highest_tp_idx == -1 else "BE STOP"
                        final["hit_level"] = desc
                        final["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                        final["exit_price"] = exit_price
                        final["quantity"] = exit_qty
                        final["pnl_usdt"] = round(pnl,4)
                        results.append(final)
                        update_portfolio({'pnl_usdt': pnl})
                        remaining_qty = 0
                        full_close_data = {
                            "symbol": sym, "action": direction,
                            "limit_price": entry, "stop_loss": stop_orig,
                            "take_profits": tps
                        }
                        alerts.append(f"🔴 {sym.replace('USDT','')} {direction} → {desc} (remaining closed)")
                        break
            if remaining_qty > 0:
                trade["quantity"] = remaining_qty
                trade["highest_tp"] = highest_tp_idx
                still_open.append(trade)
            elif full_close_data:
                send_trade_chart(full_close_data, title_suffix=f" – Closed")
        except Exception as e:
            print(f"Trade check error {trade['symbol']}: {e}")
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
    try:
        coins_data = fetch_coingecko("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1")
        if not coins_data: return {"action":"HOLD","reasoning":"CoinGecko unavailable.","summary":""}
        open_symbols = set()
        risky = 0
        try:
            odf = pd.read_csv(OPEN_TRADES_CSV)
            if not odf.empty:
                odf = odf.sort_values("timestamp").drop_duplicates("symbol",keep="last") if "timestamp" in odf.columns else odf.drop_duplicates("symbol",keep="last")
                open_symbols = set(odf["symbol"])
                risky = sum(odf["highest_tp"] == -1) if "highest_tp" in odf.columns else len(odf)
        except: pass
        if risky >= 3: return {"action":"HOLD","reasoning":f"Max 3 risky trades ({risky}).","summary":""}
        candidates = []
        for c in coins_data:
            sym = c.get("symbol","").upper()+"USDT"
            price = c.get("current_price")
            if price and price>0 and sym not in open_symbols:
                candidates.append({"symbol":sym,"price":price,"volume":c.get("total_volume",0)})
        candidates.sort(key=lambda x: x["volume"], reverse=True)
        candidates = candidates[:50]
        if not candidates: return {"action":"HOLD","reasoning":"No liquid coins.","summary":""}
        macro_bias = get_macro_bias()
        coin_data_cache = {}
        btc_df = get_klines_with_fallback("BTCUSDT", interval='4h', days=10)
        all_scored = []
        for coin in candidates:
            total, layers, trend_dir, atr = score_coin(coin["symbol"], coin["price"], coin["volume"], macro_bias, coin_data_cache, btc_df)
            coin["score"] = total; coin["atr"] = atr; coin["layers"] = layers; coin["trend_dir"] = trend_dir
            all_scored.append(coin)
        if not all_scored: return {"action":"HOLD","reasoning":"No valid scores.","summary":""}
        all_scored_sorted = sorted(all_scored, key=lambda x: abs(x["score"]), reverse=True)
        summary = " | ".join([f"{c['symbol'].replace('USDT','')}: {c['score']:.2f}" for c in all_scored_sorted[:30]])
        top5 = all_scored_sorted[:5]
        best_combined = -999
        best_signal = None
        for coin in top5:
            if abs(coin["score"]) < 0.5: continue
            direction = "LONG" if coin["score"] >= 0 else "SHORT"
            rating, reason = evaluate_deep(coin, direction, macro_bias)
            combined = abs(coin["score"]) * (rating / 5.0)
            if combined > best_combined:
                best_combined = combined
                coin["direction"] = direction
                coin["rating"] = rating
                coin["llama_reason"] = reason
                best_signal = coin
        if best_signal is None or best_combined < 1.49:
            best_all = all_scored_sorted[0] if all_scored_sorted else None
            reason = ""
            if best_all:
                layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_all["layers"].items()])
                reason = (
                    f"No strong conviction. Best internal score: {best_all['score']:.2f} for {best_all['symbol']}.\n"
                    f"Llama filter active – no candidate passed the quality check.\n"
                    f"Layers: {layer_str}\n"
                    f"Top coins: {summary}"
                )
            else:
                reason = "No valid coins to evaluate."
            return {"action":"HOLD", "reasoning": reason, "summary": summary}
        coin = best_signal
        direction = coin["direction"]
        entry = coin.get("bid", coin["price"]*0.999) if direction=="LONG" else coin.get("ask", coin["price"]*1.001)
        atr = coin["atr"]
        df_1h_recent = get_yahoo_klines(coin["symbol"], interval='1h', days=2)
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
        mults = [0.5, 1.0, 2.0, 3.0, 5.0]
        tps = []
        for m in mults:
            if direction == "LONG":
                tps.append(round(entry + m * risk_per_share, 6))
            else:
                tps.append(round(entry - m * risk_per_share, 6))
        sl_pct = abs(entry - stop)/entry*100
        post = generate_post(coin, direction, entry, stop, tps, sl_pct, qwen_reason=coin.get("llama_reason",""))
        return {
            "action": direction,
            "symbol": coin["symbol"],
            "quantity": qty,
            "limit_price": entry,
            "stop_loss": stop,
            "take_profits": tps,
            "confidence_score": compute_confidence(coin["layers"]),
            "conviction_score": abs(coin["score"]),
            "post_text": post,
            "summary": summary
        }
    except Exception as e:
        print(f"generate_signal error: {e}")
        return {"action":"HOLD","reasoning":f"Internal error: {e}","summary":""}

# ========== DARK CHART ==========
def send_trade_chart(signal, title_suffix=""):
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; import mplfinance as mpf
        sym = signal['symbol']; df = get_klines_with_fallback(sym, interval='4h', days=10)
        if df.empty: return
        style = mpf.make_mpf_style(base_mpf_style='nightclouds', facecolor='#000000', gridcolor='#2a2e39',
                                   rc={'axes.labelcolor':'white','xtick.color':'white','ytick.color':'white','axes.titlecolor':'white'})
        ema20 = df['Close'].ewm(span=20, adjust=False).mean()
        apds = [mpf.make_addplot(ema20,color='#f39c12',width=1.5,label='EMA20')]
        fig,axes=mpf.plot(df,type='candle',style=style,title=f"{sym.replace('USDT','')} 4h{title_suffix}",ylabel='Price',addplot=apds,returnfig=True,figsize=(8,6))
        ax=axes[0]
        entry=signal.get('limit_price'); stop=signal.get('stop_loss'); tps=signal.get('take_profits')
        if entry and stop:
            ax.axhline(y=entry,color='#f1c40f',linestyle='--',linewidth=1.5,label='Entry')
            ax.axhline(y=stop,color='#e74c3c',linestyle='--',linewidth=1.5,label='Stop')
            if tps:
                labels = ['TP1 (0.5R)', 'TP2 (1R)', 'TP3 (2R)', 'TP4 (3R)', 'TP5 (5R)']
                for i,tp in enumerate(tps):
                    ax.axhline(y=tp,color='#2ecc71',linestyle='--',linewidth=1,alpha=0.8,label=labels[i])
            ax.legend(loc='upper left',facecolor='#000000',edgecolor='white',labelcolor='white')
        path=f"{sym.replace('USDT','')}_chart.png"
        fig.savefig(path,dpi=150,bbox_inches='tight',facecolor='black'); plt.close(fig)
        url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(path,'rb') as img: requests.post(url,data={'chat_id':CHAT_ID},files={'photo':img})
        os.remove(path)
    except ImportError:
        base=signal['symbol'].replace("USDT","").upper(); studies="&studies[]=STD%3BEMA%3B20&studies[]=STD%3BVWAP"
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
            send_telegram(dec.get('reasoning','HOLD'))
    except Exception as e:
        err = f"Bot crashed: {traceback.format_exc()}"
        print(err); send_telegram(err[:500])

if __name__ == "__main__":
    main()