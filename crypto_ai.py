import requests, json, os, traceback, re, logging
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

# ========== COIN LIST (will be filtered to top 30 by CoinGecko volume) ==========
COIN_LIST = [
    "SOLUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "UNIUSDT", "NEARUSDT", "ATOMUSDT", "ETCUSDT",
    "STXUSDT", "FILUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "TIAUSDT", "SEIUSDT", "RUNEUSDT",
    "GRTUSDT", "AAVEUSDT", "ALGOUSDT", "SANDUSDT", "MANAUSDT", "THETAUSDT", "FTMUSDT", "EOSUSDT",
    "MKRUSDT", "LDOUSDT", "IMXUSDT", "FLOWUSDT", "XTZUSDT", "NEOUSDT", "KSMUSDT", "ZECUSDT",
    "DASHUSDT", "EGLDUSDT", "MINAUSDT", "GALAUSDT", "HNTUSDT", "CFXUSDT", "ARUSDT", "FETUSDT",
    "AGIXUSDT", "OCEANUSDT", "1INCHUSDT", "CRVUSDT", "AXSUSDT", "CHZUSDT", "ENJUSDT", "BATUSDT",
    "SNXUSDT", "COMPUSDT", "YFIUSDT", "SUSHIUSDT", "ZRXUSDT", "RENUSDT", "CELOUSDT", "LRCUSDT",
    "ANKRUSDT", "STORJUSDT", "COTIUSDT", "KAVAUSDT", "ICXUSDT", "ONTUSDT", "ZILUSDT", "WAVESUSDT",
    "QTUMUSDT", "OMGUSDT", "BANDUSDT", "DENTUSDT", "HOTUSDT", "IOSTUSDT", "RVNUSDT", "SCUSDT",
    "ZENUSDT", "CKBUSDT", "SKLUSDT", "CTSIUSDT", "CTKUSDT", "LINAUSDT", "TRBUSDT", "BALUSDT",
    "PERPUSDT", "BNTUSDT", "RSRUSDT", "TOMOUSDT", "DGBUSDT", "DUSKUSDT", "REEFUSDT", "ALPHAUSDT",
    "FORTHUSDT", "POLSUSDT", "C98USDT", "RAREUSDT", "ATAUSDT", "IDEXUSDT", "MLNUSDT",
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
    except Exception as e:
        print(f"Yahoo error for {symbol_usdt}: {e}")
        return pd.DataFrame()

# ---------- LAYER 1: TECHNICALS (Yahoo) ----------
def get_technical_scores(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='1h', days=7)
    if df.empty or len(df) < 50:
        print(f"[TECH] No 1H data for {symbol_usdt} – using neutral")
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
    rsi = 100 - (100 / (1 + rs))
    rsi_val = rsi.iloc[-1] if not rsi.empty else 50
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
        print(f"[ATR] Not enough data for {symbol_usdt}, using 2% fallback")
        return current_price * 0.02
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    if pd.isna(atr):
        print(f"[ATR] NaN for {symbol_usdt}, using 2%")
        return current_price * 0.02
    return atr

# ---------- LAYER 2: ORDER FLOW (Binance, optional) ----------
def get_order_flow(symbol_usdt):
    """Returns imbalance and a note if unavailable."""
    try:
        r = requests.get(f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol_usdt}&limit=10", timeout=5)
        if r.status_code == 200:
            data = r.json()
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if not bids or not asks:
                return 0.0, "empty books"
            bid_vol = sum(float(b[1]) for b in bids[:10])
            ask_vol = sum(float(a[1]) for a in asks[:10])
            total = bid_vol + ask_vol
            if total:
                imbalance = (bid_vol - ask_vol) / total
                return imbalance, "OK"
        return 0.0, f"status {r.status_code}"
    except Exception as e:
        return 0.0, f"error: {e}"

# ---------- LAYER 3: DERIVATIVES (Binance, optional) ----------
def get_funding(symbol_usdt):
    try:
        r = requests.get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol_usdt}", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return float(data.get("lastFundingRate", 0)) * 100, "OK"
        return 0.01, f"status {r.status_code}"
    except Exception as e:
        return 0.01, f"error: {e}"

def get_oi(symbol_usdt):
    try:
        r = requests.get(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol_usdt}", timeout=5)
        if r.status_code == 200:
            return float(r.json().get("openInterest", 0)), "OK"
        return 0.0, f"status {r.status_code}"
    except Exception as e:
        return 0.0, f"error: {e}"

# ---------- LAYER 4: ON‑CHAIN (Glassnode, needs API key) ----------
def get_on_chain(symbol_usdt):
    """Try a free Glassnode metric; will fail without key. Returns a note."""
    # We attempt exchange netflow for the asset (symbol without USDT).
    asset = symbol_usdt.replace("USDT", "")
    # Glassnode API v1 endpoint (requires key)
    # We'll try without key to demonstrate error
    try:
        r = requests.get(f"https://api.glassnode.com/v1/metrics/transactions/transfers_volume_to_exchanges_sum?a={asset}&api_key=demo", timeout=5)
        if r.status_code == 200:
            data = r.json()
            # just return a placeholder for now; not integrated in scoring
            return data, "OK"
        return None, f"Glassnode status {r.status_code} (likely needs key)"
    except Exception as e:
        return None, f"Glassnode error: {e}"

# ---------- LAYER 5: SENTIMENT (Fear & Greed, CoinGecko Trending) ----------
def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return int(data["data"][0]["value"]), data["data"][0]["value_classification"]
        return None, "API failed"
    except Exception as e:
        return None, f"error: {e}"

def is_trending(symbol_usdt):
    try:
        data = fetch_coingecko("https://api.coingecko.com/api/v3/search/trending")
        if data:
            base = symbol_usdt.replace("USDT", "")
            for item in data.get("coins", []):
                if item["item"]["symbol"].upper() == base.upper():
                    return True, "OK"
        return False, "not found or API failed"
    except Exception as e:
        return False, f"error: {e}"

# ---------- LAYER 6: MACRO (CoinGecko, DXY via yfinance) ----------
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
        }, "OK"
    return None, "CoinGecko global failed"

def get_dxy():
    try:
        df = yf.download("DX-Y.NYB", period="5d", interval="1h", progress=False)
        if df.empty:
            return None, "no data"
        # Force scalar: take the last Close value and convert to float
        last_close = df['Close'].iloc[-1]
        if hasattr(last_close, 'item'):
            dxy_value = float(last_close.item())
        else:
            dxy_value = float(last_close)
        return dxy_value, "OK"
    except Exception as e:
        return None, f"error: {e}"

# ---------- SCORING ENGINE ----------
def score_coin(symbol, price, volume_24h, change1h):
    scores = {}          # store each layer's contribution and notes
    errors = []

    # Technical (20%)
    tech = get_technical_scores(symbol)
    tech_combined = (tech["trend"] * 0.5 + tech["momentum"] * 0.3 + tech["macd"] * 0.2) / 3
    scores["tech"] = 0.20 * tech_combined

    # Volume/Momentum (10%)
    if volume_24h > 1_000_000:
        momentum = max(min(change1h, 3), -3)
        scores["vol_mom"] = 0.10 * momentum
    else:
        scores["vol_mom"] = 0.0

    # Order flow (15%)
    imbalance, order_note = get_order_flow(symbol)
    if order_note != "OK":
        errors.append(f"OrderFlow({symbol}): {order_note}")
    scores["order_flow"] = 0.15 * (imbalance * 3)

    # Derivatives (funding + OI, 10%)
    funding, fund_note = get_funding(symbol)
    oi, oi_note = get_oi(symbol)
    if fund_note != "OK":
        errors.append(f"Funding({symbol}): {fund_note}")
    if oi_note != "OK":
        errors.append(f"OI({symbol}): {oi_note}")
    # Simple scoring: positive funding -> bearish, negative -> bullish; combine with OI change (assume flat)
    funding_score = -funding if abs(funding) < 0.1 else 0
    scores["derivatives"] = 0.10 * (funding_score * 2)

    # On-chain (15%) – we can't get real data, so always 0 and note error
    onchain_data, onchain_note = get_on_chain(symbol)
    if onchain_data is None:
        errors.append(f"OnChain({symbol}): {onchain_note}")
    scores["onchain"] = 0.0   # not used

    # Sentiment (Fear & Greed + Trending, 10%)
    fg_value, fg_note = get_fear_greed()
    if fg_value is None:
        errors.append(f"FearGreed: {fg_note}")
    trending, trend_note = is_trending(symbol)
    if trend_note != "OK":
        errors.append(f"Trending({symbol}): {trend_note}")
    # Fear & Greed: <30 fear (contrarian long), >70 greed (contrarian short)
    fg_signal = 0
    if fg_value is not None:
        if fg_value < 30:
            fg_signal = 2
        elif fg_value > 70:
            fg_signal = -2
    scores["sentiment"] = 0.10 * (fg_signal + (2 if trending else 0)) / 2

    # Macro (BTC.D, DXY, 10%)
    macro, macro_note = get_macro_data()
    dxy, dxy_note = get_dxy()
    if macro is None:
        errors.append(f"Macro: {macro_note}")
    if dxy is None:
        errors.append(f"DXY: {dxy_note}")
    macro_score = 0
    if macro and dxy:
        # BTC.D low + DXY falling = bullish for alts
        if macro["btc_d"] < 55 and dxy < 100:
            macro_score = 2
        elif macro["btc_d"] > 55 and dxy > 102:
            macro_score = -1
    scores["macro"] = 0.10 * macro_score

    # Fundamentals / Catalyst (5%) – use trending as proxy
    scores["fundamentals"] = 0.05 * (2 if trending else 0)

    total = sum(scores.values())
    total = max(-3, min(3, total))

    # Return total, layer details, and error list
    return total, scores, errors

# ---------- AI REASONING (unchanged) ----------
def call_groq_reasoning(symbol, entry, atr, macro, errors=None):
    prompt = (
        f"Trade signal for {symbol} at {entry}. 1h ATR: {atr:.4f}. "
        f"Macro: {json.dumps(macro)}. "
        f"{'Data errors: ' + '; '.join(errors) if errors else ''}"
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
    # 1. CoinGecko universe screening (top 30 liquid altcoins)
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
    candidates = candidates[:30]   # top 30 liquid

    if not candidates:
        return {"action": "HOLD", "reasoning": "No liquid coins in predefined list."}

    best = None
    best_score = 0
    best_layers = None
    best_errors = []
    macro, _ = get_macro_data()

    for coin in candidates:
        sym = coin["symbol"]
        price = coin["price"]
        volume = coin["volume"]

        # 1h change
        df_1h = get_yahoo_klines(sym, interval='1h', days=2)
        change1h = 0.0
        if not df_1h.empty and len(df_1h) >= 2:
            closes = df_1h['Close']
            if len(closes) >= 2:
                prev, curr = float(closes.iloc[-2]), float(closes.iloc[-1])
                if prev > 0:
                    change1h = ((curr - prev) / prev) * 100.0

        total_score, layers, errors = score_coin(sym, price, volume, change1h)
        atr = get_1h_atr(sym, price)
        coin["score"] = total_score
        coin["atr"] = atr
        coin["bid"] = price * 0.999
        coin["ask"] = price * 1.001
        coin["layers"] = layers
        coin["errors"] = errors

        if best is None or abs(total_score) > abs(best_score):
            best = coin
            best_score = total_score
            best_layers = layers
            best_errors = errors

    # ----- HOLD with detailed breakdown -----
    if best is None or abs(best_score) < 1.5:
        best_sym = best["symbol"] if best else "none"
        layer_str = ""
        if best_layers:
            layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
        errors_str = "; ".join(best_errors) if best_errors else "none"
        reason = (f"No strong conviction. Best score: {best_score:.2f} for {best_sym}. "
                  f"Layers: {layer_str}. Errors: {errors_str}")
        return {"action": "HOLD", "reasoning": reason}

    direction = "LONG" if best_score >= 0 else "SHORT"
    # 4H trend gate (Yahoo)
    trend_4h = 'neutral'
    df_4h = get_yahoo_klines(best["symbol"], interval='1h', days=30)
    if not df_4h.empty:
        df_4h_res = df_4h.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
        if len(df_4h_res) >= 50:
            closes_4h = df_4h_res['Close']
            ema50 = closes_4h.ewm(50).mean()
            ema200 = closes_4h.ewm(200).mean() if len(closes_4h)>=200 else ema50
            trend_4h = 'up' if ema50.iloc[-1] > ema200.iloc[-1] else 'down'
    if (direction == "LONG" and trend_4h == "down") or (direction == "SHORT" and trend_4h == "up"):
        layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
        errors_str = "; ".join(best_errors) if best_errors else "none"
        reason = (f"Signal {direction} contradicts 4H trend ({trend_4h}). "
                  f"Best score: {best_score:.2f}. Layers: {layer_str}. Errors: {errors_str}")
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

    errors_str = "; ".join(best_errors) if best_errors else "none"
    conf, reason = call_groq_reasoning(best["symbol"], entry, atr, macro, best_errors)
    if conf < 6:
        layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
        reason = (f"AI confidence too low ({conf}/10). Best score: {best_score:.2f}. "
                  f"Layers: {layer_str}. Errors: {errors_str}. {reason}")
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
        "reasoning": f"{reason} | Errors: {errors_str}",
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
