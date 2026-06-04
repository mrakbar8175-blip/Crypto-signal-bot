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

# ========== COIN UNIVERSE (99 altcoins) ==========
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

def get_yahoo_klines(symbol_usdt, interval='4h', days=60):
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

# ========== LAYER 1: TECHNICALS (4h) – weight 15% ==========
def get_technicals(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=60)
    if df.empty or len(df) < 50:
        return {"trend": 0, "adx": 0, "macd": 0, "structure": 0, "combined": 0}

    closes = df['Close']
    highs  = df['High']
    lows   = df['Low']

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

    # ADX
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

    # MACD
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

    # PRICE ACTION (stricter structure)
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
        trend * 0.25 +
        adx_score * 0.25 +
        macd_score * 0.15 +
        structure_score * 0.35
    )

    return {
        "trend": trend,
        "adx": adx_score,
        "macd": macd_score,
        "structure": structure_score,
        "combined": combined
    }

def get_4h_atr(symbol_usdt, current_price):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=60)
    if df.empty or len(df) < 14:
        return current_price * 0.02
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    return atr if not pd.isna(atr) else current_price * 0.02

# ========== LAYER 2: BUYING PRESSURE (4h volume) – weight 30% ==========
def get_buying_pressure(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=10)
    if df.empty or len(df) < 24:
        return 0.0
    df = df.tail(24)
    buy_vol = df.loc[df['Close'] > df['Open'], 'Volume'].sum()
    sell_vol = df.loc[df['Close'] <= df['Open'], 'Volume'].sum()
    total = buy_vol + sell_vol
    if total == 0:
        return 0.0
    return (buy_vol - sell_vol) / total

# ========== LAYER 3: VOLATILITY (4h) – weight 10% ==========
def get_volatility_score(symbol_usdt, current_price):
    atr = get_4h_atr(symbol_usdt, current_price)
    atr_pct = atr / current_price * 100
    if atr_pct < 1:
        return -1
    elif atr_pct > 12:
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

# ========== LAYER 5: SENTIMENT (trend‑aware, 4h trend) – weight 20% ==========
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

def get_4h_trend_direction(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=60)
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
    trend_dir = get_4h_trend_direction(symbol_usdt)

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
    tech_combined = tech["combined"]

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
        f"Trade signal for {symbol} at {entry}. 4h ATR: {atr:.4f}. "
        f"Macro: BTC.D {macro.get('btc_d')}, DXY {macro.get('dxy')}. "
        f"Layer scores: {layer_str}. "
        "Provide a detailed reasoning (2-3 sentences) covering chart setup, macro environment, and trade management. "
        "Also give a confidence 1-10.\n"
        "Format: CONFIDENCE: 7 | REASONING: [text]"
    )
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 300
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

    all_scored = []
    best = None
    best_score = 0
    best_layers = None
    macro = get_macro()

    for coin in candidates:
        sym = coin["symbol"]
        price = coin["price"]
        volume = coin["volume"]

        total_score, layers = score_coin(sym, price, volume, 0)
        atr = get_4h_atr(sym, price)
        coin["score"] = total_score
        coin["atr"] = atr
        coin["bid"] = price * 0.999
        coin["ask"] = price * 1.001
        coin["layers"] = layers

        all_scored.append(coin)

        if best is None or abs(total_score) > abs(best_score):
            best = coin
            best_score = total_score
            best_layers = layers

    # Build a sorted summary of ALL 30 coins
    all_scored_sorted = sorted(all_scored, key=lambda x: abs(x["score"]), reverse=True)
    coin_summary_list = []
    for c in all_scored_sorted:
        coin_summary_list.append(f"{c['symbol'].replace('USDT','')}: {c['score']:.2f}")
    coin_summary = " | ".join(coin_summary_list)

    if best is None or abs(best_score) < 1.49:
        best_sym = best["symbol"] if best else "none"
        layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
        reason = (f"No strong conviction. Best score: {best_score:.2f} for {best_sym}.\n"
                  f"Layers: {layer_str}\n"
                  f"All coins: {coin_summary}")
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
        reason = (f"AI confidence too low ({conf}/10). Best score: {best_score:.2f} for {best['symbol']}.\n"
                  f"Layers: {layer_str}\n"
                  f"All coins: {coin_summary}\n{reason}")
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
            setup_icon = "📈" if action == "LONG" else "📉"
            entry_price = dec.get('limit_price', 0)
            stop_price = dec.get('stop_loss', 0)
            confidence = dec.get('confidence_score', 0)
            conviction = dec.get('conviction_score', 0)
            reasoning = dec.get('reasoning', '')
            tps = dec.get('take_profits', [])

            entry_low  = round(entry_price * 0.995, 4)
            entry_high = round(entry_price * 1.005, 4)

            tp_lines = f"📌 TP1: ${tps[0]:,.4f} (Book partials + Move SL to Break-Even)\n"
            for i in range(1, len(tps)):
                tp_lines += f"📌 TP{i+1}: ${tps[i]:,.4f}\n"
            tp_lines = tp_lines.strip()

            msg = (
                f"🚨 {symbol} Trade Setup! Full Trade Plan Inside!\n\n"
                f"Position: {action} {direction_icon}\n"
                f"Setup Type: Swing Trade {setup_icon}\n\n"
                f"⛔ ENTRY: CMP — ${entry_price:,.4f} (or within ${entry_low:,.4f} - ${entry_high:,.4f})\n\n"
                f"🛑 STOP LOSS: ${stop_price:,.4f} (Invalidation level)\n\n"
                f"🎯 TAKE-PROFIT TARGETS:\n"
                f"{tp_lines}\n\n"
                f"📊 CONVICTION: {conviction:.2f}  |  🤖 AI CONFIDENCE: {confidence}/10\n\n"
                f"🧠 TECHNICAL & MACRO BREAKDOWN:\n"
                f"{reasoning}\n\n"
                f"Trade Management: Once TP1 hits, secure partial profits and move stop-loss to entry. "
                f"Let the rest run risk-free.\n\n"
                f"⚠️ Disclaimer: NFA (Not Financial Advice) | DYOR (Do Your Own Research) | Manage your risk"
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
