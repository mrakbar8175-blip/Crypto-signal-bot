import requests, json, os, traceback, time, re
import pandas as pd
import numpy as np

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

# ========== DATA HELPERS ==========
def fetch_binance(endpoint):
    try:
        r = requests.get("https://fapi.binance.com" + endpoint, timeout=10)
        return r.json()
    except:
        return {}

def fetch_coingecko(url):
    try:
        r = requests.get(url, timeout=15)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

def get_binance_last_price(symbol):
    ticker = fetch_binance(f"/fapi/v1/ticker/price?symbol={symbol}")
    if "price" in ticker:
        return float(ticker["price"])
    return 0

# ---------- TECHNICAL INDICATORS ----------
def get_klines_df(symbol, interval, limit=100):
    endpoint = f"/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = fetch_binance(endpoint)
    if not data or "error" in data:
        return pd.DataFrame()
    df = pd.DataFrame(data, columns=['open_time', 'open', 'high', 'low', 'close', 'volume',
                                     'close_time', 'quote_vol', 'trades', 'taker_buy_base',
                                     'taker_buy_quote', 'ignore'])
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return df

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if not rsi.empty else 50

def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def get_4h_trend(symbol):
    df = get_klines_df(symbol, '4h', limit=200)
    if df.empty or len(df) < 200:
        return 'neutral'
    closes = df['close']
    ema50 = compute_ema(closes, 50)
    ema200 = compute_ema(closes, 200)
    if ema50.iloc[-1] > ema200.iloc[-1]:
        return 'up'
    elif ema50.iloc[-1] < ema200.iloc[-1]:
        return 'down'
    return 'neutral'

def get_technical_scores(symbol):
    df_1h = get_klines_df(symbol, '1h', limit=100)
    df_15m = get_klines_df(symbol, '15m', limit=100)

    trend_score = 0
    momentum_score = 0
    macd_score = 0

    if not df_1h.empty and len(df_1h) >= 50:
        closes_1h = df_1h['close']
        ema50_1h = compute_ema(closes_1h, 50)
        ema200_1h = compute_ema(closes_1h, 200) if len(closes_1h) >= 200 else ema50_1h

        current_price = closes_1h.iloc[-1]
        if current_price > ema50_1h.iloc[-1]:
            trend_score += 1.5
        else:
            trend_score -= 1.5
        if ema50_1h.iloc[-1] > ema200_1h.iloc[-1]:
            trend_score += 1.5
        else:
            trend_score -= 1.5
        trend_score = max(-3, min(3, trend_score))

        rsi_1h = compute_rsi(closes_1h, 14)
        if rsi_1h < 30:
            momentum_score = 2
        elif rsi_1h > 70:
            momentum_score = -2
        elif rsi_1h > 60:
            momentum_score = 1
        elif rsi_1h < 40:
            momentum_score = -1
        else:
            momentum_score = 0

        ema12 = compute_ema(closes_1h, 12)
        ema26 = compute_ema(closes_1h, 26)
        macd_line = ema12 - ema26
        signal_line = compute_ema(macd_line, 9)
        histogram = macd_line - signal_line
        if len(histogram) > 2:
            hist_now = histogram.iloc[-1]
            hist_prev = histogram.iloc[-2]
            if hist_now > 0 and hist_prev <= 0:
                macd_score = 2
            elif hist_now < 0 and hist_prev >= 0:
                macd_score = -2
            elif hist_now > 0:
                macd_score = 1
            elif hist_now < 0:
                macd_score = -1
    else:
        if not df_15m.empty and len(df_15m) >= 50:
            closes_15m = df_15m['close']
            ema50_15m = compute_ema(closes_15m, 50)
            current_price = closes_15m.iloc[-1]
            if current_price > ema50_15m.iloc[-1]:
                trend_score = 1
            else:
                trend_score = -1
            rsi_15m = compute_rsi(closes_15m, 14)
            if rsi_15m < 30:
                momentum_score = 2
            elif rsi_15m > 70:
                momentum_score = -2
            else:
                momentum_score = 0

    return {
        "trend_score": trend_score,
        "momentum_score": momentum_score,
        "macd_score": macd_score
    }

# ---------- ATR (1h) ----------
def get_1h_atr(symbol, current_price):
    df = get_klines_df(symbol, '1h', limit=50)
    if not df.empty and len(df) >= 14:
        high = df['high']
        low = df['low']
        close = df['close']
        tr = pd.concat([high - low,
                        (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        if not pd.isna(atr):
            return atr
    return current_price * 0.02

# ---------- ORDER BOOK IMBALANCE ----------
def get_imbalance(symbol, fallback_price):
    depth = fetch_binance(f"/fapi/v1/depth?symbol={symbol}&limit=10")
    if "bids" in depth and "asks" in depth:
        bids = depth["bids"]
        asks = depth["asks"]
        bid_vol = sum(float(b[1]) for b in bids[:10])
        ask_vol = sum(float(a[1]) for a in asks[:10])
        total = bid_vol + ask_vol
        if total:
            return (bid_vol - ask_vol) / total
    return 0.0

# ---------- FUNDING ----------
def get_funding(symbol):
    data = fetch_binance(f"/fapi/v1/premiumIndex?symbol={symbol}")
    if "lastFundingRate" in data:
        return float(data["lastFundingRate"]) * 100
    return 0.01

# ---------- MACRO ----------
def get_macro_data():
    data = fetch_coingecko("https://api.coingecko.com/api/v3/global")
    if data:
        total_mcap = data["data"]["total_market_cap"]["usd"]
        btc_mcap = data["data"]["market_cap_percentage"]["btc"]
        others = data["data"]["market_cap_percentage"].get("others", 10)
        total2 = total_mcap * (1 - btc_mcap/100)
        total3 = total_mcap * (others/100)
        btc_d = btc_mcap
        usdt_d = data["data"]["market_cap_percentage"].get("usdt", 3)
        return {"total2": total2, "total3": total3, "btc_d": btc_d, "usdt_d": usdt_d}
    return None

# ---------- 1‑HOUR CHANGE ----------
def get_1h_change(symbol):
    df = get_klines_df(symbol, '1h', limit=2)
    if df.empty or len(df) < 2:
        return 0.0
    prev_close = df['close'].iloc[-2]
    current_close = df['close'].iloc[-1]
    if prev_close == 0:
        return 0.0
    return ((current_close - prev_close) / prev_close) * 100.0

# ---------- SCORING ----------
def score_coin(symbol, bid, ask, vol, change1h):
    score = 0.0

    tech = get_technical_scores(symbol)
    tech_combined = (tech["trend_score"] * 0.5 +
                     tech["momentum_score"] * 0.3 +
                     tech["macd_score"] * 0.2) / 3
    score += 0.30 * tech_combined

    imbalance = get_imbalance(symbol, bid)
    if abs(imbalance) > 0.5:
        score += 0.15 * (imbalance * 3)
    elif abs(imbalance) > 0.2:
        score += 0.15 * (imbalance * 3) * 0.5

    if vol > 1_000_000:
        momentum = max(min(change1h, 3), -3)
        score += 0.10 * momentum

    funding = get_funding(symbol)
    if abs(funding) < 0.05:
        score += 0.10 * 2
    elif abs(funding) < 0.1:
        score += 0.10 * 1

    macro = get_macro_data()
    trend_4h = get_4h_trend(symbol)
    if macro and trend_4h != 'neutral':
        btc_d = macro["btc_d"]
        if trend_4h == 'up':
            if btc_d < 55:
                score += 0.20 * 2
            elif btc_d < 60:
                score += 0.20 * 1
        else:
            if btc_d > 55:
                score += 0.20 * (-1)
            else:
                score += 0.20 * (-2)

    try:
        trending = fetch_coingecko("https://api.coingecko.com/api/v3/search/trending")
        if trending:
            coin_id = symbol.lower()
            for item in trending.get("coins", []):
                if item["item"]["symbol"].upper() == symbol:
                    score += 0.05 * 2
                    break
    except:
        pass

    return max(-3, min(3, score))

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
    # Fetch top 100 from CoinGecko
    url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1"
    coins_data = fetch_coingecko(url)
    if not coins_data:
        return {"action": "HOLD", "reasoning": "CoinGecko API unavailable."}

    # Excluded symbols (stablecoins, wrapped, and your specified ones)
    excluded_symbols = {"usdt", "usdc", "busd", "dai", "wbtc", "weth", "steth", "btcb", "wbnb",
                        "btc", "eth", "link", "ltc", "matic"}

    candidates = []
    for coin in coins_data:
        symbol = coin.get("symbol", "").upper()
        if symbol in excluded_symbols or len(symbol) > 10:
            continue
        binance_symbol = symbol + "USDT"
        price = get_binance_last_price(binance_symbol)
        if price <= 0:
            continue
        vol = coin.get("total_volume", 0)
        change1h = get_1h_change(binance_symbol)
        candidates.append({
            "symbol": binance_symbol,
            "price": price,
            "volume": vol,
            "change1h": change1h
        })
        if len(candidates) >= 50:
            break

    if not candidates:
        return {"action": "HOLD", "reasoning": "No liquid perp markets found."}

    macro = get_macro_data()
    best = None
    best_score = 0
    for coin in candidates:
        sym = coin["symbol"]
        bid = coin["price"] * 0.999
        ask = coin["price"] * 1.001
        score = score_coin(sym, bid, ask, coin["volume"], coin["change1h"])
        atr = get_1h_atr(sym, coin["price"])
        coin["score"] = score
        coin["atr"] = atr
        coin["bid"] = bid
        coin["ask"] = ask
        if best is None or abs(score) > abs(best_score):
            best = coin
            best_score = score

    if best is None or abs(best_score) < 1.5:
        return {"action": "HOLD", "reasoning": f"No strong conviction. Best score: {best_score:.2f} for {best['symbol'] if best else 'none'}."}

    direction = "LONG" if best_score >= 0 else "SHORT"

    trend_4h = get_4h_trend(best["symbol"])
    if (direction == "LONG" and trend_4h == "down") or (direction == "SHORT" and trend_4h == "up"):
        return {"action": "HOLD", "reasoning": f"Signal {direction} contradicts 4H trend ({trend_4h}). Best score: {best_score:.2f}"}

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
        return {"action": "HOLD", "reasoning": f"AI confidence too low ({conf}/10). {reason}"}

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
        print("Telegram fail:", e)

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
            tps = [
                dec.get('take_profit_1', 0),
                dec.get('take_profit_2', 0),
                dec.get('take_profit_3', 0),
                dec.get('take_profit_4', 0),
                dec.get('take_profit_5', 0),
                dec.get('take_profit_6', 0),
            ]

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
        err_msg = f"Fatal: {traceback.format_exc()}"
        print(err_msg)
        send_telegram(err_msg[:500])

if __name__ == "__main__":
    main()
