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

# ========== LAYER 1: TECHNICALS (upgraded) – weight 15% ==========
def get_technicals(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='1h', days=7)
    if df.empty or len(df) < 50:
        return {"trend": 0, "adx": 0, "macd": 0, "structure": 0}

    closes = df['Close']
    highs  = df['High']
    lows   = df['Low']

    # ---------- EMA trend (simple, reliable) ----------
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

    # ---------- ADX (trend strength, replaces RSI) ----------
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

    # ADX scoring: only give points if trend is strong (ADX > 25)
    adx_score = 0
    if adx_now > 25:
        if di_plus_now > di_minus_now:
            adx_score = 2.5   # strong uptrend
        else:
            adx_score = -2.5  # strong downtrend
    elif adx_now > 20:
        if di_plus_now > di_minus_now:
            adx_score = 1.0
        else:
            adx_score = -1.0
    # else stay 0 (ranging/no trend)

    # ---------- MACD (simplified – just above/below zero) ----------
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal
    macd_now = histogram.iloc[-1]
    if macd_now > 0:
        macd_score = 1.5
    elif macd_now < 0:
        macd_score = -1.5
    else:
        macd_score = 0

    # ---------- PRICE ACTION (stricter structure, window=7) ----------
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
        # also require at least two consecutive higher highs and higher lows for a strong uptrend
        if last_hh and last_hl:
            # check if the previous swing also was higher to confirm
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
            # potential downtrend
            if len(swing_highs) >= 3 and len(swing_lows) >= 3:
                prev_lh = swing_highs[-2][1] < swing_highs[-3][1]
                prev_ll = swing_lows[-2][1] < swing_lows[-3][1]
                if prev_lh and prev_ll:
                    structure_score = -3.0
                else:
                    structure_score = -2.0
            else:
                structure_score = -2.0
        else:
            structure_score = 0
    structure_score = max(-3, min(3, structure_score))

    # ---------- Combine with new weights ----------
    # trend 25%, adx 25%, macd 15%, structure 35%
    combined = (
        trend * 0.25 +
        adx_score * 0.25 +
        macd_score * 0.15 +
        structure_score * 0.35
    )
    # scale to roughly -3..3 (it already is)

    return {
        "trend": trend,
        "adx": adx_score,
        "macd": macd_score,
        "structure": structure_score,
        "combined": combined
    }

def get_1h_atr(symbol_usdt, current_price):
    df = get_yahoo_klines(symbol_usdt, interval='1h', days=7)
    if df.empty or len(df) < 14:
        return current_price * 0.02
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    return atr if not pd.isna(atr) else current_price * 0.02

# ========== LAYER 2: BUYING PRESSURE (Yahoo) – weight 30% ==========
def get_buying_pressure(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='1h', days=2)
    if df.empty or len(df) < 24:
        return 0.0
    df = df.tail(24)
    buy_vol = df.loc[df['Close'] > df['Open'], 'Volume'].sum()
    sell_vol = df.loc[df['Close'] <= df['Open'], 'Volume'].sum()
    total = buy_vol + sell_vol
    if total == 0:
        return 0.0
    return (buy_vol - sell_vol) / total

# ========== LAYER 3: VOLATILITY (Yahoo) – weight 10% ==========
def get_volatility_score(symbol_usdt, current_price):
    atr = get_1h_atr(symbol_usdt, current_price)
    atr_pct = atr / current_price * 100
    if atr_pct < 1:
        return -1
    elif atr_pct > 8:
        return -1
    else:
        return 1

# ========== LAYER 4: MACRO (CoinGecko + DXY) – weight 25% ==========
def get_macro():
    cg = fetch_coingecko("https://api.coingecko.com/api/v3/global")
    btc_d = None
    dxy = None
    if cg:
        btc_d = cg["data"]["market_cap_percentage"]["btc"]
    try:
        df = yf.download("DX-Y.NYB", period="5d", interval="1h", progress=False)
        if not df.empty:
            val = df['Close'].iloc[-1]
            dxy = float(val.item()) if hasattr(val, 'item') else float(val)
    except:
        pass
    return {"btc_d": btc_d, "dxy": dxy}

def macro_score(macro):
    if macro["btc_d"] is None or macro["dxy"] is None:
        return 0
    score = 0
    if macro["btc_d"] < 55:
        score += 2
    elif macro["btc_d"] > 60:
        score -= 1
    if macro["dxy"] < 100:
        score += 1
    else:
        score -= 1
    return max(-3, min(3, score))

# ========== LAYER 5: SENTIMENT (trend‑aware) – weight 20% ==========
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

def get_1h_trend_direction(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='1h', days=7)
    if df.empty or len(df) < 50:
        return 'neutral'
    closes = df['Close']
    ema50 = closes.ewm(span=50, adjust=False).mean()
    ema200 = closes.ewm(span=200, adjust=False).mean() if len(closes) >= 200 else ema50
    if ema50.iloc[-1] > ema200.iloc[-1]:
        return 'up'
    elif ema50.iloc[-1] < ema200.iloc[-1]:
        return 'down'
    return 'neutral'

def sentiment_score(symbol_usdt):
    fg_value, _ = get_fear_greed()
    trending = is_trending(symbol_usdt)
    trend_dir = get_1h_trend_direction(symbol_usdt)

    score = 0
    if fg_value < 30 and trend_dir != 'down':
        score += 2
    elif fg_value > 70 and trend_dir != 'up':
        score -= 2

    if trending:
        score += 1

    return max(-3, min(3, score))

# ========== SCORING ENGINE ==========
def score_coin(symbol, price, volume_24h, change1h):
    tech = get_technicals(symbol)
    tech_combined = tech["combined"]   # already scaled

    buying = get_buying_pressure(symbol)
    buying_score = buying * 3

    vol_score = get_volatility_score(symbol, price)

    macro = get_macro()
    macro_s = macro_score(macro)

    sent_s = sentiment_score(symbol)

    total = (
        0.15 * tech_combined +
        0.30 * buying_score +
        0.10 * vol_score +
        0.25 * macro_s +
        0.20 * sent_s
    )

    layers = {
        "tech": tech_combined,
        "buying_press": buying_score,
        "volatility": vol_score,
        "macro": macro_s,
        "sentiment": sent_s,
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
    except:
        pass
    return 6, "Multi-factor model (AI unavailable)."

# ========== MAIN SIGNAL GENERATION ==========
def generate_signal():
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

        total_score, layers = score_coin(sym, price, volume, 0)
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

    if best is None or abs(best_score) < 1.49:
        best_sym = best["symbol"] if best else "none"
        layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
        reason = f"No strong conviction. Best score: {best_score:.2f} for {best_sym}. Layers: {layer_str}"
        return {"action": "HOLD", "reasoning": reason}

    direction = "LONG" if best_score >= 0 else "SHORT"
    entry = best["bid"] if direction == "LONG" else best["ask"]
    atr = best["atr"]
    min_stop = max(1.5 * atr, entry * 0.02)
    stop = entry - min_stop if direction == "LONG" else entry + min_stop
    stop = round(stop, 6)
    risk = abs(entry - stop)
    qty = round(10 / risk, 4)

    mults = [0.4, 0.8, 1.2, 1.6, 2.0]
    tps = []
    for mult in mults:
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
        "limit_price": entry,
        "stop_loss": stop,
        "take_profits": tps,
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
            tps = dec.get('take_profits', [])

            tp_lines = "\n".join([f"📌 ${tp:,.4f}" for tp in tps])

            msg = (
                f"{symbol} ‼️\n\n"
                f"{action} {direction_icon}\n\n"
                f"⛔ ENTRY: CMP — ${entry_price:,.4f}\n\n"
                f"🛑 STOP LOSS: ${stop_price:,.4f}\n\n"
                f"🎯 TARGETS:\n"
                f"{tp_lines}\n\n"
                f"📊 CONVICTION: {conviction:.2f}  |  🤖 AI CONFIDENCE: {confidence}/10\n\n"
                f"🔄 After TP1 is hit, move stop loss to breakeven & book partial profits.\n"
                f"💰 Let the remaining position run for higher targets.\n\n"
                f"🧠 WHY: {reasoning}\n\n"
                f"⚠️ NFA | DYOR | Manage your risk"
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
