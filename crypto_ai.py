import requests, json, os, traceback, re
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

# ========== PAPER PORTFOLIO ==========
portfolio = {
    "balance_usdt": 1000.0,
    "positions": [],
    "realized_pnl": 0.0,
    "daily_loss_limit": -20
}

# ========== FULL COIN LIST (we'll filter to top 30 by volume) ==========
COIN_LIST = [
    "SOLUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT",
    "UNIUSDT", "NEARUSDT", "ATOMUSDT", "ETCUSDT",
    "STXUSDT", "FILUSDT", "ARBUSDT", "OPUSDT",
    "INJUSDT", "TIAUSDT", "SEIUSDT", "RUNEUSDT",
    "GRTUSDT", "AAVEUSDT", "ALGOUSDT", "SANDUSDT", "MANAUSDT",
    "THETAUSDT", "FTMUSDT", "EOSUSDT", "MKRUSDT", "LDOUSDT",
    "IMXUSDT", "FLOWUSDT", "XTZUSDT", "NEOUSDT", "KSMUSDT",
    "ZECUSDT", "DASHUSDT", "EGLDUSDT", "MINAUSDT", "GALAUSDT",
    "HNTUSDT", "CFXUSDT", "ARUSDT", "FETUSDT", "AGIXUSDT",
    "OCEANUSDT", "1INCHUSDT", "CRVUSDT",
    "AXSUSDT", "CHZUSDT", "ENJUSDT", "BATUSDT", "SNXUSDT",
    "COMPUSDT", "YFIUSDT", "SUSHIUSDT", "ZRXUSDT", "RENUSDT",
    "CELOUSDT", "LRCUSDT", "ANKRUSDT", "STORJUSDT", "COTIUSDT",
    "KAVAUSDT", "ICXUSDT", "ONTUSDT", "ZILUSDT", "WAVESUSDT",
    "QTUMUSDT", "OMGUSDT", "BANDUSDT", "DENTUSDT", "HOTUSDT",
    "IOSTUSDT", "RVNUSDT", "SCUSDT", "ZENUSDT", "CKBUSDT",
    "SKLUSDT", "CTSIUSDT", "CTKUSDT", "LINAUSDT", "TRBUSDT",
    "BALUSDT", "PERPUSDT", "BNTUSDT", "RSRUSDT", "TOMOUSDT",
    "DGBUSDT", "DUSKUSDT", "REEFUSDT", "ALPHAUSDT", "FORTHUSDT",
    "POLSUSDT", "C98USDT", "RAREUSDT", "ATAUSDT", "IDEXUSDT",
    "MLNUSDT",
]

# ========== DATA HELPERS ==========
def fetch_coingecko(url, retries=2):
    for _ in range(retries):
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
        except:
            pass
    return {}

def get_yahoo_klines(symbol_usdt, interval='1h', days=7):
    yahoo_symbol = symbol_usdt.replace("USDT", "-USD")
    end = datetime.now()
    start = end - timedelta(days=days)
    try:
        df = yf.download(yahoo_symbol, start=start, end=end, interval=interval, progress=False)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except:
        return pd.DataFrame()

# ---------- TECHNICALS ----------
def compute_rsi(series, period=14):
    if len(series) < period + 1:
        return 50
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if not rsi.empty else 50

def compute_ema(series, period):
    if len(series) < period:
        return series.iloc[-1] if len(series) > 0 else 0
    return series.ewm(span=period, adjust=False).mean()

def get_4h_trend_from_yahoo(symbol_usdt):
    df_1h = get_yahoo_klines(symbol_usdt, interval='1h', days=30)
    if df_1h.empty or len(df_1h) < 50:
        return 'neutral'
    df_4h = df_1h.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
    if len(df_4h) < 50:
        return 'neutral'
    closes = df_4h['Close']
    ema50 = compute_ema(closes, 50)
    ema200 = compute_ema(closes, 200) if len(closes) >= 200 else ema50
    if ema50.iloc[-1] > ema200.iloc[-1]:
        return 'up'
    elif ema50.iloc[-1] < ema200.iloc[-1]:
        return 'down'
    return 'neutral'

def get_technical_scores_from_yahoo(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='1h', days=7)
    if df.empty or len(df) < 50:
        return {"trend": 0, "momentum": 0, "macd": 0}
    closes = df['Close']
    # trend
    ema50 = compute_ema(closes, 50) if len(closes) >= 50 else closes.iloc[-1]
    ema200 = compute_ema(closes, 200) if len(closes) >= 200 else ema50
    current = closes.iloc[-1]
    trend_raw = 0
    if current > ema50.iloc[-1]:
        trend_raw += 1.5
    else:
        trend_raw -= 1.5
    if ema50.iloc[-1] > ema200.iloc[-1]:
        trend_raw += 1.5
    else:
        trend_raw -= 1.5
    trend = max(-3, min(3, trend_raw))
    # momentum (RSI)
    rsi = compute_rsi(closes, 14)
    if rsi < 30:
        momentum = 2
    elif rsi > 70:
        momentum = -2
    elif rsi > 60:
        momentum = 1
    elif rsi < 40:
        momentum = -1
    else:
        momentum = 0
    # MACD
    ema12 = compute_ema(closes, 12)
    ema26 = compute_ema(closes, 26)
    macd_line = ema12 - ema26
    signal_line = compute_ema(macd_line, 9)
    histogram = macd_line - signal_line
    macd = 0
    if len(histogram) >= 2:
        hist_now = histogram.iloc[-1]
        hist_prev = histogram.iloc[-2]
        if hist_now > 0 and hist_prev <= 0:
            macd = 2
        elif hist_now < 0 and hist_prev >= 0:
            macd = -2
        elif hist_now > 0:
            macd = 1
        elif hist_now < 0:
            macd = -1
    return {"trend": trend, "momentum": momentum, "macd": macd}

def get_1h_atr_from_yahoo(symbol_usdt, current_price):
    df = get_yahoo_klines(symbol_usdt, interval='1h', days=7)
    if df.empty or len(df) < 14:
        return current_price * 0.02
    high = df['High']
    low = df['Low']
    close = df['Close']
    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    return atr if not pd.isna(atr) else current_price * 0.02

# ---------- MACRO & TRENDING ----------
def get_macro_data():
    data = fetch_coingecko("https://api.coingecko.com/api/v3/global")
    if data:
        total_mcap = data["data"]["total_market_cap"]["usd"]
        btc_mcap = data["data"]["market_cap_percentage"]["btc"]
        others = data["data"]["market_cap_percentage"].get("others", 10)
        return {
            "total2": total_mcap * (1 - btc_mcap/100),
            "total3": total_mcap * (others/100),
            "btc_d": btc_mcap,
            "usdt_d": data["data"]["market_cap_percentage"].get("usdt", 3)
        }
    return None

def is_trending(symbol_usdt):
    try:
        trending = fetch_coingecko("https://api.coingecko.com/api/v3/search/trending")
        if trending:
            base = symbol_usdt.replace("USDT", "")
            for item in trending.get("coins", []):
                if item["item"]["symbol"].upper() == base.upper():
                    return True
    except:
        pass
    return False

# ---------- SCORING (returns both final score and layer breakdown) ----------
def score_coin(symbol, price, volume_24h, change1h):
    tech = get_technical_scores_from_yahoo(symbol)
    tech_combined = (tech["trend"] * 0.5 + tech["momentum"] * 0.3 + tech["macd"] * 0.2) / 3
    score_tech = 0.45 * tech_combined

    # Volume & Momentum (15%)
    score_vol = 0.0
    if volume_24h > 1_000_000:
        momentum = max(min(change1h, 3), -3)
        score_vol = 0.15 * momentum

    # Macro (35%)
    macro = get_macro_data()
    trend_4h = get_4h_trend_from_yahoo(symbol)
    score_macro = 0.0
    if macro and trend_4h != 'neutral':
        btc_d = macro["btc_d"]
        if trend_4h == 'up':
            if btc_d < 55:
                score_macro = 0.35 * 2
            elif btc_d < 60:
                score_macro = 0.35 * 1
        else:  # down
            if btc_d > 55:
                score_macro = 0.35 * (-1)
            else:
                score_macro = 0.35 * (-2)

    # Trending (5%)
    score_trending = 0.05 * 2 if is_trending(symbol) else 0.0

    total = score_tech + score_vol + score_macro + score_trending
    total = max(-3, min(3, total))

    layers = {
        "trend": tech["trend"],
        "momentum": tech["momentum"],
        "macd": tech["macd"],
        "vol_momentum": score_vol,
        "macro": score_macro,
        "trending": score_trending
    }
    return total, layers

# ---------- AI REASONING ----------
def call_groq_reasoning(symbol, entry, atr, macro):
    prompt = (
        f"Trade signal for {symbol} at {entry}. 1h ATR: {atr:.4f}. "
        f"Macro: {json.dumps(macro)}. Provide a short reasoning and confidence 1-10.\n"
        "Format: CONFIDENCE: 7 | REASONING: [text]"
    )
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 100
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"]
            conf_match = re.search(r'CONFIDENCE:\s*(\d+)', text)
            reason_match = re.search(r'REASONING:\s*(.*)', text)
            conf = int(conf_match.group(1)) if conf_match else 6
            reason = reason_match.group(1).strip() if reason_match else "Automated quantitative signal."
            return conf, reason
    except:
        pass
    return 6, "Multi-factor model signal."

# ========== MAIN SIGNAL GENERATION ==========
def generate_signal():
    # 1. CoinGecko top 100 by volume, then filter to our list and keep top 30 liquid
    cg_url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1"
    coins_data = fetch_coingecko(cg_url)
    if not coins_data:
        return {"action": "HOLD", "reasoning": "CoinGecko market data unavailable."}

    # Map symbol -> price & volume
    cg_map = {}
    for coin in coins_data:
        sym = coin.get("symbol", "").upper() + "USDT"
        if coin.get("current_price", 0) > 0:
            cg_map[sym] = {
                "price": coin["current_price"],
                "volume": coin.get("total_volume", 0)
            }

    # Build candidate list from our static list, but only those present in cg_map
    candidates = []
    for sym in COIN_LIST:
        if sym not in cg_map:
            continue
        candidates.append({
            "symbol": sym,
            "price": cg_map[sym]["price"],
            "volume": cg_map[sym]["volume"]
        })

    if not candidates:
        return {"action": "HOLD", "reasoning": "No coins with valid prices found."}

    # Sort by volume descending and keep top 30
    candidates.sort(key=lambda x: x["volume"], reverse=True)
    candidates = candidates[:30]

    macro = get_macro_data()
    best = None
    best_score = 0
    best_layers = None

    for coin in candidates:
        sym = coin["symbol"]
        price = coin["price"]
        volume = coin["volume"]

        # 1‑hour change (safe scalar)
        df_1h = get_yahoo_klines(sym, interval='1h', days=2)
        change1h = 0.0
        if not df_1h.empty and len(df_1h) >= 2:
            close_col = df_1h['Close']
            if len(close_col) >= 2:
                prev = close_col.iloc[-2]
                curr = close_col.iloc[-1]
                if isinstance(prev, (pd.Series, np.ndarray)):
                    prev = prev.item() if hasattr(prev, 'item') else float(prev)
                if isinstance(curr, (pd.Series, np.ndarray)):
                    curr = curr.item() if hasattr(curr, 'item') else float(curr)
                if prev > 0:
                    change1h = ((curr - prev) / prev) * 100.0

        total_score, layers = score_coin(sym, price, volume, change1h)
        atr = get_1h_atr_from_yahoo(sym, price)
        coin["score"] = total_score
        coin["atr"] = atr
        coin["bid"] = price * 0.999
        coin["ask"] = price * 1.001
        coin["layers"] = layers

        if best is None or abs(total_score) > abs(best_score):
            best = coin
            best_score = total_score
            best_layers = layers

    # ----- HOLD with detailed reasoning -----
    if best is None or abs(best_score) < 1.5:
        best_sym = best["symbol"] if best else "none"
        layer_str = ""
        if best_layers:
            layer_str = (
                f"Layers: "
                f"Trend={best_layers['trend']:.1f}, "
                f"Momentum(RSI)={best_layers['momentum']:.1f}, "
                f"MACD={best_layers['macd']:.1f}, "
                f"Vol/Mom={best_layers['vol_momentum']:.2f}, "
                f"Macro={best_layers['macro']:.2f}, "
                f"Trending={best_layers['trending']:.2f}"
            )
        return {"action": "HOLD", "reasoning": f"No strong conviction. Best score: {best_score:.2f} for {best_sym}. {layer_str}"}

    direction = "LONG" if best_score >= 0 else "SHORT"
    trend_4h = get_4h_trend_from_yahoo(best["symbol"])
    if (direction == "LONG" and trend_4h == "down") or (direction == "SHORT" and trend_4h == "up"):
        layer_str = ""
        if best_layers:
            layer_str = (
                f"Layers: "
                f"Trend={best_layers['trend']:.1f}, "
                f"Momentum={best_layers['momentum']:.1f}, "
                f"MACD={best_layers['macd']:.1f}, "
                f"Vol/Mom={best_layers['vol_momentum']:.2f}, "
                f"Macro={best_layers['macro']:.2f}, "
                f"Trending={best_layers['trending']:.2f}"
            )
        return {"action": "HOLD", "reasoning": f"Signal {direction} contradicts 4H trend ({trend_4h}). Best score: {best_score:.2f} for {best['symbol']}. {layer_str}"}

    entry = best["bid"] if direction == "LONG" else best["ask"]
    atr = best["atr"]
    min_stop = max(1.5 * atr, entry * 0.02)
    stop = entry - min_stop if direction == "LONG" else entry + min_stop
    stop = round(stop, 6)
    risk = abs(entry - stop)
    qty = round(10 / risk, 4)

    tps = []
    for mult in [0.2, 0.4, 0.8, 1.2, 1.6, 2.5]:
        if direction == "LONG":
            tps.append(round(entry + mult * risk, 6))
        else:
            tps.append(round(entry - mult * risk, 6))

    conf, reason = call_groq_reasoning(best["symbol"], entry, atr, macro)
    if conf < 6:
        layer_str = ""
        if best_layers:
            layer_str = (
                f"Layers: "
                f"Trend={best_layers['trend']:.1f}, "
                f"Momentum={best_layers['momentum']:.1f}, "
                f"MACD={best_layers['macd']:.1f}, "
                f"Vol/Mom={best_layers['vol_momentum']:.2f}, "
                f"Macro={best_layers['macro']:.2f}, "
                f"Trending={best_layers['trending']:.2f}"
            )
        return {"action": "HOLD", "reasoning": f"AI confidence too low ({conf}/10). Best score: {best_score:.2f} for {best['symbol']}. {layer_str} {reason}"}

    return {
        "action": direction,
        "symbol": best["symbol"],
        "quantity": qty,
        "order_type": "LIMIT",
        "limit_price": entry,
        "stop_loss": stop,
        "take_profit_1": tps[0],
        "take_profit_2": tps[1],
        "take_profit_3": tps[2],
        "take_profit_4": tps[3],
        "take_profit_5": tps[4],
        "take_profit_6": tps[5],
        "confidence_score": conf,
        "reasoning": reason,
        "conviction_score": best_score
    }

# ========== TELEGRAM ==========
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("Telegram send failed:", e)

def main():
    try:
        dec = generate_signal()
        action = dec.get('action', 'HOLD')
        if action in ["LONG", "SHORT"]:
            raw_symbol = dec.get('symbol', '')
            symbol = raw_symbol.replace("USDT", "/USDT") if raw_symbol else ""
            direction_icon = "🟢" if action == "LONG" else "🛑"
            entry_price = dec.get('limit_price', 0)
            stop_price = dec.get('stop_loss', 0)
            confidence = dec.get('confidence_score', 0)
            conviction = dec.get('conviction_score', 0)
            reasoning = dec.get('reasoning', '')
            tps = [dec.get(f'take_profit_{i}', 0) for i in range(1,7)]
            tp_lines = "\n".join([f"📌 ${tp:,.2f}" if tp else "📌 —" for tp in tps])
            msg = (
                f"{symbol} ‼️\n\n"
                f"{action} {direction_icon}\n\n"
                f"ENTRY ⛔ LIMIT ${entry_price:,.2f}\n\n"
                f"Stoploss 🛑 ${stop_price:,.2f}\n\n"
                f"Targets 🎯\n"
                f"{tp_lines}\n\n"
                f"Conviction Score: {conviction:.2f} | Confidence: {confidence}/10\n\n"
                f"Stoploss 🛑 at breakeven when we hit our Second Target 🎯 ‼️\n\n"
                f"Reason: {reasoning}"
            )
        else:
            msg = f"📊 HOLD\nReason: {dec.get('reasoning', 'No signal')}"
        print(msg)
        send_telegram(msg)
    except Exception as e:
        err_msg = f"Bot crashed: {traceback.format_exc()}"
        print(err_msg)
        send_telegram(err_msg[:500])

if __name__ == "__main__":
    main()
