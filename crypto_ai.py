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

# ========== COIN UNIVERSE ==========
COIN_LIST = [
    "SOLUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "NEARUSDT",
    "ATOMUSDT", "ETCUSDT", "STXUSDT", "FILUSDT", "ARBUSDT",
    "OPUSDT", "INJUSDT", "TIAUSDT", "SEIUSDT", "RUNEUSDT",
    "GRTUSDT", "AAVEUSDT", "ALGOUSDT", "SANDUSDT", "MANAUSDT",
    "THETAUSDT", "FTMUSDT", "EOSUSDT", "MKRUSDT", "LDOUSDT",
    "IMXUSDT", "FLOWUSDT", "XTZUSDT", "NEOUSDT", "KSMUSDT",
    "ZECUSDT", "DASHUSDT", "EGLDUSDT", "MINAUSDT", "GALAUSDT",
    "HNTUSDT", "CFXUSDT", "ARUSDT", "FETUSDT", "AGIXUSDT",
    "OCEANUSDT", "1INCHUSDT", "CRVUSDT", "AXSUSDT", "CHZUSDT",
    "ENJUSDT", "BATUSDT", "SNXUSDT", "COMPUSDT", "YFIUSDT",
    "SUSHIUSDT", "ZRXUSDT", "RENUSDT", "CELOUSDT", "LRCUSDT",
    "ANKRUSDT", "STORJUSDT", "COTIUSDT", "KAVAUSDT", "ICXUSDT",
    "ONTUSDT", "ZILUSDT", "WAVESUSDT", "QTUMUSDT", "OMGUSDT",
    "BANDUSDT", "DENTUSDT", "HOTUSDT", "IOSTUSDT", "RVNUSDT",
    "SCUSDT", "ZENUSDT", "CKBUSDT", "SKLUSDT", "CTSIUSDT",
    "CTKUSDT", "LINAUSDT", "TRBUSDT", "BALUSDT", "PERPUSDT",
    "BNTUSDT", "RSRUSDT", "TOMOUSDT", "DGBUSDT", "DUSKUSDT",
    "REEFUSDT", "ALPHAUSDT", "FORTHUSDT", "POLSUSDT", "C98USDT",
    "RAREUSDT", "ATAUSDT", "IDEXUSDT", "MLNUSDT",
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
    return None

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

# ========== LAYER 1: TECHNICALS (Yahoo 1h) ==========
def get_technicals(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='1h', days=7)
    if df.empty or len(df) < 50:
        return {"trend": 0, "momentum": 0, "macd": 0}

    closes = df['Close']
    # EMA trend
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

    # RSI momentum
    delta = closes.diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi_val = 100 - (100 / (1 + rs)).iloc[-1] if not rs.empty else 50
    if rsi_val < 30:
        momentum = 2
    elif rsi_val > 70:
        momentum = -2
    elif rsi_val > 60:
        momentum = 1
    elif rsi_val < 40:
        momentum = -1
    else:
        momentum = 0

    # MACD
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal
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

def get_1h_atr(symbol_usdt, current_price):
    df = get_yahoo_klines(symbol_usdt, interval='1h', days=7)
    if df.empty or len(df) < 14:
        return current_price * 0.02
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    return atr if not pd.isna(atr) else current_price * 0.02

# ========== LAYER 2: ORDER FLOW / BUYING PRESSURE (Yahoo) ==========
def get_buying_pressure(symbol_usdt):
    """Approximate order flow with volume‑weighted price direction over the last 24 candles."""
    df = get_yahoo_klines(symbol_usdt, interval='1h', days=2)
    if df.empty or len(df) < 24:
        return 0.0
    df = df.tail(24)
    # For each hour, if close > open, consider it buy volume; else sell volume
    buy_vol = df.loc[df['Close'] > df['Open'], 'Volume'].sum()
    sell_vol = df.loc[df['Close'] <= df['Open'], 'Volume'].sum()
    total = buy_vol + sell_vol
    if total == 0:
        return 0.0
    return (buy_vol - sell_vol) / total   # -1 to 1

# ========== LAYER 3: VOLATILITY (Yahoo ATR%) ==========
def get_volatility_score(symbol_usdt, current_price):
    atr = get_1h_atr(symbol_usdt, current_price)
    atr_pct = atr / current_price * 100
    if atr_pct < 1:
        return -1   # too quiet
    elif atr_pct > 8:
        return -1   # too wild
    else:
        return 1    # sweet spot

# ========== LAYER 4: MACRO (CoinGecko + DXY via Yahoo) ==========
def get_macro():
    # CoinGecko global
    cg = fetch_coingecko("https://api.coingecko.com/api/v3/global")
    btc_d = None
    total2 = None
    total3 = None
    if cg:
        total_mcap = cg["data"]["total_market_cap"]["usd"]
        btc_d = cg["data"]["market_cap_percentage"]["btc"]
        others = cg["data"]["market_cap_percentage"].get("others", 10)
        total2 = total_mcap * (1 - btc_d/100)
        total3 = total_mcap * (others/100)
    # DXY
    dxy = None
    try:
        df = yf.download("DX-Y.NYB", period="5d", interval="1h", progress=False)
        if not df.empty:
            val = df['Close'].iloc[-1]
            if hasattr(val, 'item'):
                dxy = float(val.item())
            else:
                dxy = float(val)
    except:
        pass
    return {"btc_d": btc_d, "total2": total2, "total3": total3, "dxy": dxy}

def macro_score(macro):
    """Return a score from -3 to 3 based on macro conditions."""
    if not macro["btc_d"] or macro["dxy"] is None:
        return 0
    score = 0
    if macro["btc_d"] < 55:
        score += 2   # alt season
    elif macro["btc_d"] > 60:
        score -= 1   # BTC heavy
    if macro["dxy"] < 100:
        score += 1   # risk‑on
    else:
        score -= 1
    return max(-3, min(3, score))

# ========== LAYER 5: SENTIMENT (Fear & Greed + CoinGecko Trending) ==========
def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return int(data["data"][0]["value"]), data["data"][0]["value_classification"]
    except:
        pass
    return 50, "neutral"

def is_trending(symbol_usdt):
    data = fetch_coingecko("https://api.coingecko.com/api/v3/search/trending")
    if data:
        base = symbol_usdt.replace("USDT", "")
        for item in data.get("coins", []):
            if item["item"]["symbol"].upper() == base.upper():
                return True
    return False

def sentiment_score(symbol_usdt):
    fg_value, _ = get_fear_greed()
    trending = is_trending(symbol_usdt)
    score = 0
    # Fear & Greed: <30 fear (contrarian long), >70 greed (contrarian short)
    if fg_value < 30:
        score += 2
    elif fg_value > 70:
        score -= 2
    if trending:
        score += 1
    return max(-3, min(3, score))

# ========== LAYER 6: VOLUME / MOMENTUM ==========
def volume_momentum_score(volume_24h, change1h):
    score = 0
    if volume_24h > 1_000_000:
        # change1h capped at ±3
        momentum = max(min(change1h, 3), -3)
        score = momentum
    return max(-3, min(3, score))

# ========== LAYER 7: ON‑CHAIN PROXY ==========
# We don't have on‑chain, so we use a composite of volatility + trending
def onchain_proxy_score(symbol_usdt, current_price):
    # ATR% already computed, but we can reuse volatility_score
    vol_score = get_volatility_score(symbol_usdt, current_price)
    trending = is_trending(symbol_usdt)
    return (vol_score + (1 if trending else 0)) / 2

# ========== SCORING ENGINE ==========
def score_coin(symbol, price, volume_24h, change1h):
    tech = get_technicals(symbol)
    tech_combined = (tech["trend"] * 0.5 + tech["momentum"] * 0.3 + tech["macd"] * 0.2) / 3

    buying = get_buying_pressure(symbol)
    vol_mom = volume_momentum_score(volume_24h, change1h)
    vol_score = get_volatility_score(symbol, price)
    macro = get_macro()
    macro_s = macro_score(macro)
    sent_s = sentiment_score(symbol)
    onchain_s = onchain_proxy_score(symbol, price)

    # Weights (sum to 1.0)
    total = (
        0.20 * tech_combined +
        0.15 * buying * 3 +
        0.10 * vol_mom +
        0.10 * vol_score +
        0.20 * macro_s / 3 * 3 +   # scale macro_s to -3..3 (already is)
        0.10 * sent_s / 3 * 3 +
        0.05 * onchain_s * 3 +
        0.10 * (0.0)  # reserved
    )
    # Actually, we need to ensure all components are -3..3 scale. Adjust:
    total = (
        0.20 * tech_combined +
        0.15 * max(min(buying * 3, 3), -3) +
        0.10 * vol_mom +
        0.10 * vol_score +
        0.20 * macro_s +
        0.10 * sent_s +
        0.05 * onchain_s * 3
    )

    layers = {
        "tech": tech_combined,
        "buying_press": buying * 3,
        "vol_mom": vol_mom,
        "volatility": vol_score,
        "macro": macro_s,
        "sentiment": sent_s,
        "onchain_proxy": onchain_s * 3,
    }
    return max(-3, min(3, total)), layers

# ========== AI REASONING ==========
def call_groq_reasoning(symbol, entry, atr, macro, layers):
    layer_str = "; ".join([f"{k}={v:.2f}" for k,v in layers.items()])
    prompt = (
        f"Trade signal for {symbol} at {entry}. 1h ATR: {atr:.4f}. "
        f"Macro: BTC.D {macro.get('btc_d')}, DXY {macro.get('dxy')}. "
        f"Layer scores: {layer_str}. "
        "Provide a short reasoning and confidence 1-10.\n"
        "Format: CONFIDENCE: 7 | REASONING: [text]"
    )
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 150
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"]
            conf_match = re.search(r'CONFIDENCE:\s*(\d+)', text)
            reason_match = re.search(r'REASONING:\s*(.*)', text)
            conf = int(conf_match.group(1)) if conf_match else 6
            reason = reason_match.group(1).strip() if reason_match else "Automated signal."
            return conf, reason
    except Exception as e:
        print(f"Groq error: {e}")
    return 6, "Multi-factor model (AI unavailable)."

# ========== MAIN SIGNAL GENERATION ==========
def generate_signal():
    # 1. Universe screening (top 30 by CoinGecko volume)
    cg_url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1"
    coins_data = fetch_coingecko(cg_url)
    if not coins_data:
        return {"action": "HOLD", "reasoning": "CoinGecko market data unavailable."}

    cg_map = {}
    for coin in coins_data:
        sym = coin.get("symbol", "").upper() + "USDT"
        if coin.get("current_price", 0) > 0:
            cg_map[sym] = {"price": coin["current_price"], "volume": coin.get("total_volume", 0)}

    candidates = []
    for sym in COIN_LIST:
        if sym not in cg_map:
            continue
        candidates.append({"symbol": sym, "price": cg_map[sym]["price"], "volume": cg_map[sym]["volume"]})
    candidates.sort(key=lambda x: x["volume"], reverse=True)
    candidates = candidates[:30]

    if not candidates:
        return {"action": "HOLD", "reasoning": "No liquid coins in predefined list."}

    best = None
    best_score = 0
    best_layers = None
    macro = get_macro()

    for coin in candidates:
        sym = coin["symbol"]
        price = coin["price"]
        volume = coin["volume"]

        # 1h change (safe scalar)
        df_1h = get_yahoo_klines(sym, interval='1h', days=2)
        change1h = 0.0
        if not df_1h.empty and len(df_1h) >= 2:
            closes = df_1h['Close']
            if len(closes) >= 2:
                prev = float(closes.iloc[-2])
                curr = float(closes.iloc[-1])
                if prev > 0:
                    change1h = ((curr - prev) / prev) * 100.0

        total_score, layers = score_coin(sym, price, volume, change1h)
        atr = get_1h_atr(sym, price)
        coin["score"] = total_score
        coin["atr"] = atr
        coin["bid"] = price * 0.999
        coin["ask"] = price * 1.001
        coin["layers"] = layers

        if best is None or abs(total_score) > abs(best_score):
            best = coin
            best_score = total_score
            best_layers = layers

    # ----- HOLD with detailed layer breakdown -----
    if best is None or abs(best_score) < 1.5:
        best_sym = best["symbol"] if best else "none"
        layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
        reason = f"No strong conviction. Best score: {best_score:.2f} for {best_sym}. Layers: {layer_str}"
        return {"action": "HOLD", "reasoning": reason}

    direction = "LONG" if best_score >= 0 else "SHORT"
    # 4H trend gate
    trend_4h = 'neutral'
    df_4h = get_yahoo_klines(best["symbol"], interval='1h', days=30)
    if not df_4h.empty and len(df_4h) >= 50:
        df_4h_res = df_4h.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
        if len(df_4h_res) >= 50:
            closes_4h = df_4h_res['Close']
            ema50 = closes_4h.ewm(50).mean()
            ema200 = closes_4h.ewm(200).mean() if len(closes_4h)>=200 else ema50
            if ema50.iloc[-1] > ema200.iloc[-1]:
                trend_4h = 'up'
            else:
                trend_4h = 'down'
    if (direction == "LONG" and trend_4h == "down") or (direction == "SHORT" and trend_4h == "up"):
        layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
        reason = f"Signal {direction} contradicts 4H trend ({trend_4h}). Best score: {best_score:.2f}. Layers: {layer_str}"
        return {"action": "HOLD", "reasoning": reason}

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

    conf, reason = call_groq_reasoning(best["symbol"], entry, atr, macro, best_layers)
    if conf < 6:
        layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
        reason = f"AI confidence too low ({conf}/10). Best score: {best_score:.2f}. Layers: {layer_str}. {reason}"
        return {"action": "HOLD", "reasoning": reason}

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
        "conviction_score": best_score,
        "layers": best_layers
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
