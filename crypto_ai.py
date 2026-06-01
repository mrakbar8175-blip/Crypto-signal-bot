import requests, json, os, traceback, time, re

# ---------- ENVIRONMENT ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not set in secrets.")

# ---------- PAPER PORTFOLIO ----------
portfolio = {
    "balance_usdt": 1000.0,
    "positions": [],
    "realized_pnl": 0.0,
    "daily_loss_limit": -20
}

# ---------- EXPANDED COIN MAPPING (30+ altcoins) ----------
COIN_MAP = {
    "binancecoin": "BNBUSDT", "ripple": "XRPUSDT", "cardano": "ADAUSDT",
    "solana": "SOLUSDT", "dogecoin": "DOGEUSDT", "polkadot": "DOTUSDT",
    "uniswap": "UNIUSDT", "avalanche-2": "AVAXUSDT", "near": "NEARUSDT",
    "cosmos": "ATOMUSDT", "ethereum-classic": "ETCUSDT", "stellar": "XLMUSDT",
    "vechain": "VETUSDT", "filecoin": "FILUSDT", "aptos": "APTUSDT",
    "arbitrum": "ARBUSDT", "optimism": "OPUSDT", "injective-protocol": "INJUSDT",
    "celestia": "TIAUSDT", "sei-network": "SEIUSDT", "sui": "SUIUSDT",
    "thorchain": "RUNEUSDT", "the-graph": "GRTUSDT", "aave": "AAVEUSDT",
    "algorand": "ALGOUSDT", "the-sandbox": "SANDUSDT", "decentraland": "MANAUSDT",
    "theta-token": "THETAUSDT", "fantom": "FTMUSDT", "eos": "EOSUSDT",
    "maker": "MKRUSDT", "lido-dao": "LDOUSDT", "immutable-x": "IMXUSDT",
    "flow": "FLOWUSDT", "tezos": "XTZUSDT", "neo": "NEOUSDT",
    "kusama": "KSMUSDT", "zcash": "ZECUSDT", "dash": "DASHUSDT",
    "elrond-erd-2": "EGLDUSDT", "mina-protocol": "MINAUSDT", "gala": "GALAUSDT",
    "helium": "HNTUSDT", "conflux-token": "CFXUSDT", "arweave": "ARUSDT",
    "fetch-ai": "FETUSDT", "singularitynet": "AGIXUSDT", "ocean-protocol": "OCEANUSDT",
    "1inch": "1INCHUSDT", "curve-dao-token": "CRVUSDT",
}

# ---------- DATA HELPERS ----------
def fetch_binance(endpoint):
    try:
        r = requests.get("https://fapi.binance.com" + endpoint, timeout=10)
        return r.json()
    except:
        return {"error": "request failed"}

def fetch_coingecko(url):
    try:
        r = requests.get(url, timeout=15)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

def get_binance_last_price(symbol):
    """Fallback price directly from Binance ticker."""
    ticker = fetch_binance(f"/fapi/v1/ticker/price?symbol={symbol}")
    if "price" in ticker:
        return float(ticker["price"])
    return 0

def calculate_rsi(closes, period=14):
    if len(closes) < period+1: return 50
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d,0) for d in deltas]
    losses = [max(-d,0) for d in deltas]
    avg_gain = sum(gains[-period:])/period
    avg_loss = sum(losses[-period:])/period
    if avg_loss == 0: return 100
    rs = avg_gain/avg_loss
    return 100 - (100/(1+rs))

def compute_ema(data, period):
    if len(data) < period: return data[-1] if data else 0
    k = 2/(period+1)
    ema = sum(data[:period])/period
    for price in data[period:]:
        ema = (price - ema)*k + ema
    return ema

def get_4h_atr(symbol, current_bid):
    klines = fetch_binance(f"/fapi/v1/klines?symbol={symbol}&interval=4h&limit=50")
    if isinstance(klines, list) and len(klines) >= 14:
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]
        trs = []
        for i in range(1, len(klines)):
            h, l, prev_c = highs[i], lows[i], closes[i-1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        return sum(trs[-14:]) / 14
    return current_bid * 0.02

def get_multi_tf_trend(symbol):
    signals = []
    for interval, limit in [("4h", 50), ("1h", 50)]:
        klines = fetch_binance(f"/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}")
        if isinstance(klines, list) and len(klines) >= limit:
            closes = [float(k[4]) for k in klines]
            ema50 = compute_ema(closes, 50) if len(closes)>=50 else closes[-1]
            if closes[-1] > ema50:
                signals.append(1)
            else:
                signals.append(-1)
        else:
            signals.append(0)
    avg = sum(signals)/len(signals) if signals else 0
    if avg > 0.5: return 1
    if avg < -0.5: return -1
    return 0

def get_order_book_imbalance(symbol, fallback_price=None):
    depth = fetch_binance(f"/fapi/v1/depth?symbol={symbol}&limit=10")
    if "bids" in depth and "asks" in depth:
        bids = depth["bids"]
        asks = depth["asks"]
        bid_vol_10 = sum(float(b[1]) for b in bids[:10])
        ask_vol_10 = sum(float(a[1]) for a in asks[:10])
        total = bid_vol_10 + ask_vol_10
        imbalance = (bid_vol_10 - ask_vol_10) / total if total else 0
        return imbalance, float(bids[0][0]), float(asks[0][0])
    # Fallback price from CoinGecko or Binance
    if fallback_price and fallback_price > 0:
        return 0, fallback_price * 0.9999, fallback_price * 1.0001
    # Try Binance last price
    price = get_binance_last_price(symbol)
    if price > 0:
        return 0, price * 0.9999, price * 1.0001
    return None, None, None

def get_funding(symbol):
    data = fetch_binance(f"/fapi/v1/premiumIndex?symbol={symbol}")
    if "lastFundingRate" in data:
        return float(data["lastFundingRate"]) * 100
    return 0.01

def get_24h_metrics(symbol):
    ticker = fetch_binance(f"/fapi/v1/ticker/24hr?symbol={symbol}")
    if "symbol" in ticker:
        return float(ticker.get("quoteVolume", 0)), float(ticker.get("priceChangePercent", 0))
    return 1000000, 0

def get_trending(coin_id):
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
        if resp.status_code == 200:
            trending = resp.json()
            for item in trending.get("coins", []):
                if item["item"]["id"] == coin_id:
                    return True
    except:
        pass
    return False

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
        dxy = 104.5
        return {
            "total_mcap": total_mcap,
            "total2": total2,
            "total3": total3,
            "btc_d": btc_d,
            "usdt_d": usdt_d,
            "dxy": dxy
        }
    return None

# ---------- SIMPLIFIED SCORING ----------
def score_coin(symbol, bid, ask, fallback_price):
    score = 0.0
    trend = get_multi_tf_trend(symbol)
    score += 0.25 * trend * 3
    imbalance, _, _ = get_order_book_imbalance(symbol, fallback_price)
    if imbalance is not None:
        score += 0.15 * (imbalance * 3)
    vol, change = get_24h_metrics(symbol)
    if vol > 1_000_000:
        score += 0.10 * (min(change/10, 3) if change > 0 else max(change/10, -3))
    funding = get_funding(symbol)
    if abs(funding) < 0.05:
        score += 0.10 * 2
    elif abs(funding) < 0.1:
        score += 0.10 * 1
    macro = get_macro_data()
    if macro:
        if macro["btc_d"] < 55:
            score += 0.20 * 2
        elif macro["btc_d"] < 60:
            score += 0.20 * 1
    coin_id = [k for k, v in COIN_MAP.items() if v == symbol][0]
    if get_trending(coin_id):
        score += 0.05 * 2
    return max(-3, min(3, score))

# ---------- AI FOR REASONING ----------
def call_groq_for_reasoning(symbol, bid, ask, atr, macro):
    prompt = (
        f"You are a crypto trading desk analyst. A trade signal has been generated for {symbol}.\n"
        f"Entry: {bid} (long) or {ask} (short). 4h ATR: {atr:.6f}. Macro data: {json.dumps(macro)}.\n"
        "Based on your knowledge of current market conditions and this data, provide a short reasoning (1-2 sentences) "
        "and a confidence score from 1 to 10 for this trade. "
        "Reply EXACTLY in this format:\n"
        "CONFIDENCE: 7 | REASONING: The trend is up, order book shows buying pressure, and macro is supportive."
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
            reason = reason_match.group(1).strip() if reason_match else "Signal generated by quantitative model."
            return conf, reason
    except Exception as e:
        print(f"Groq reasoning error: {e}")
    return 6, "Automated signal based on real-time data."

# ---------- MAIN SIGNAL GENERATION ----------
def generate_signal():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1"
        resp = requests.get(url, timeout=15)
        all_coins = resp.json() if resp.status_code == 200 else []
    except:
        all_coins = []

    excluded_ids = {"bitcoin", "ethereum", "chainlink", "litecoin", "matic-network"}
    candidates = [coin for coin in all_coins if coin["id"] not in excluded_ids and coin["id"] in COIN_MAP]
    candidates.sort(key=lambda x: x.get("total_volume", 0), reverse=True)
    top_coins = candidates[:10]

    if not top_coins:
        return {"action": "HOLD", "reasoning": "No altcoins available in the current universe."}

    best_coin = None
    best_score = -999
    best_data = {}
    macro = get_macro_data()

    for coin in top_coins:
        sym = COIN_MAP[coin["id"]]
        fallback_price = coin.get("current_price", 0)
        # If CoinGecko didn't give a price, get it from Binance
        if not fallback_price or fallback_price == 0:
            fallback_price = get_binance_last_price(sym)
        imbalance, bid, ask = get_order_book_imbalance(sym, fallback_price)
        if bid is None:
            continue
        score = score_coin(sym, bid, ask, fallback_price)
        atr = get_4h_atr(sym, bid)
        data = {
            "symbol": sym,
            "bid": bid,
            "ask": ask,
            "score": score,
            "atr": atr
        }
        if abs(score) > abs(best_score):
            best_score = score
            best_coin = sym
            best_data = data

    if best_coin is None or abs(best_score) < 1.5:
        return {"action": "HOLD", "reasoning": f"No strong conviction. Highest score: {best_score:.2f} for {best_coin or 'none'}."}

    direction = "LONG" if best_score > 0 else "SHORT"
    entry = best_data["bid"] if direction == "LONG" else best_data["ask"]
    atr = best_data["atr"]

    min_stop = max(1.5 * atr, entry * 0.02)
    stop = entry - min_stop if direction == "LONG" else entry + min_stop
    stop = round(stop, 6)

    risk = abs(entry - stop)
    qty = round(10 / risk, 4)

    tps = []
    for r_mult in [0.2, 0.4, 0.8, 1.2, 1.6, 2.5]:
        if direction == "LONG":
            tps.append(round(entry + r_mult * risk, 6))
        else:
            tps.append(round(entry - r_mult * risk, 6))

    conf, reason = call_groq_for_reasoning(best_coin, entry, best_data["ask"], atr, macro)
    if conf < 6:
        return {"action": "HOLD", "reasoning": f"AI confidence too low ({conf}/10). {reason}"}

    return {
        "action": direction,
        "symbol": best_coin,
        "quantity": qty,
        "order_type": "MARKET",
        "limit_price": entry,
        "stop_loss": stop,
        "take_profit_1": tps[0],
        "take_profit_2": tps[1],
        "take_profit_3": tps[2],
        "take_profit_4": tps[3],
        "take_profit_5": tps[4],
        "take_profit_6": tps[5],
        "confidence_score": conf,
        "reasoning": reason
    }

# ---------- TELEGRAM ----------
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
            msg = (f"📊 {action} {dec.get('symbol')}\n"
                   f"Entry: MARKET @ {dec.get('limit_price','CMP')}\n"
                   f"Stop: {dec.get('stop_loss')}\n"
                   f"TPs: {dec.get('take_profit_1')} | {dec.get('take_profit_2')} | {dec.get('take_profit_3')} | {dec.get('take_profit_4')} | {dec.get('take_profit_5')} | {dec.get('take_profit_6')}\n"
                   f"Qty: {dec.get('quantity')} | Confidence: {dec.get('confidence_score')}/10\n"
                   f"Reason: {dec.get('reasoning')}")
        else:
            msg = f"📊 HOLD\nReason: {dec.get('reasoning','No signal')}"
        print(msg)
        send_telegram(msg)
    except Exception as e:
        err_msg = f"Fatal: {traceback.format_exc()}"
        print(err_msg)
        send_telegram(err_msg[:500])

if __name__ == "__main__":
    main()
