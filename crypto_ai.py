import requests, json, os, traceback

# ---------- ENVIRONMENT ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]

# ---------- PAPER PORTFOLIO ----------
portfolio = {
    "balance_usdt": 1000.0,
    "positions": [],
    "realized_pnl": 0.0,
    "daily_loss_limit": -50
}

# ---------- CONSTANTS ----------
COIN_MAP = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "binancecoin": "BNBUSDT",
    "ripple": "XRPUSDT",
    "cardano": "ADAUSDT",
    "solana": "SOLUSDT",
    "dogecoin": "DOGEUSDT",
    "polkadot": "DOTUSDT",
    "matic-network": "MATICUSDT",
    "chainlink": "LINKUSDT",
    "uniswap": "UNIUSDT",
    "avalanche-2": "AVAXUSDT",
    "litecoin": "LTCUSDT",
    "cosmos": "ATOMUSDT",
    "ethereum-classic": "ETCUSDT"
}

# ---------- DATA HELPERS ----------
def fetch_binance(endpoint):
    try:
        r = requests.get("https://fapi.binance.com" + endpoint, timeout=10)
        return r.json()
    except:
        return {"error": "request failed"}

def get_cg_price(coin_id):
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json().get(coin_id, {}).get("usd", 0)
    except:
        pass
    return 0

def get_order_book_data(symbol):
    depth = fetch_binance(f"/fapi/v1/depth?symbol={symbol}&limit=5")
    if "bids" in depth and "asks" in depth:
        bids, asks = depth["bids"], depth["asks"]
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        spread_pct = (ask - bid) / ask * 100
        bid_vol = sum(float(b[1]) for b in bids[:3])
        ask_vol = sum(float(a[1]) for a in asks[:3])
        total = bid_vol + ask_vol
        imbalance = (bid_vol - ask_vol) / total if total else 0
    else:
        coin_id = [k for k, v in COIN_MAP.items() if v == symbol][0]
        price = get_cg_price(coin_id)
        if price == 0:
            return None
        bid = price * 0.9999
        ask = price * 1.0001
        spread_pct = 0.02
        imbalance = 0

    klines = fetch_binance(f"/fapi/v1/klines?symbol={symbol}&interval=1m&limit=60")
    if isinstance(klines, list) and len(klines) >= 60:
        cv, cvp = 0, 0
        trs = []
        for i in range(len(klines)-14, len(klines)):
            h, l, c = float(klines[i][2]), float(klines[i][3]), float(klines[i][4])
            v = float(klines[i][5])
            tp = (h + l + c) / 3
            cvp += tp * v
            cv += v
            prev_c = float(klines[i-1][4])
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        vwap = cvp / cv if cv else bid
        atr = sum(trs) / len(trs)
    else:
        vwap = bid
        atr = bid * 0.015

    ticker = fetch_binance(f"/fapi/v1/ticker/24hr?symbol={symbol}")
    if "symbol" in ticker:
        volume = float(ticker.get("quoteVolume", 0))
        change = float(ticker.get("priceChangePercent", 0))
    else:
        volume = 1000000
        change = 0

    return {
        "symbol": symbol,
        "bid": bid, "ask": ask,
        "spread_pct": spread_pct,
        "orderbook_imbalance": imbalance,
        "vwap_1h": vwap,
        "atr_14": atr,
        "24h_volume": volume,
        "24h_change_pct": change
    }

def get_funding(symbol):
    data = fetch_binance(f"/fapi/v1/premiumIndex?symbol={symbol}")
    if "lastFundingRate" in data:
        return float(data["lastFundingRate"]) * 100
    return 0.01

def get_ls_ratio(symbol):
    data = fetch_binance(f"/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m")
    if isinstance(data, list) and len(data) > 0 and "longShortRatio" in data[0]:
        return float(data[0]["longShortRatio"])
    return None

def get_oi(symbol):
    data = fetch_binance(f"/fapi/v1/openInterest?symbol={symbol}")
    if "openInterest" in data:
        return float(data["openInterest"])
    return 0

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

def gather_market_data(symbols):
    results = []
    for sym in symbols:
        print(f"Fetching {sym}...")
        base = get_order_book_data(sym)
        if base is None:
            continue
        base["funding_rate"] = get_funding(sym)
        base["long_short_ratio"] = get_ls_ratio(sym)
        base["open_interest"] = get_oi(sym)
        coin_id = [k for k, v in COIN_MAP.items() if v == sym][0]
        base["is_trending"] = get_trending(coin_id)
        results.append(base)
    return results

# ---------- DEEPSEEK API CALL ----------
def call_deepseek(prompt_text):
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",       # fast & reliable
        "messages": [
            {"role": "user", "content": prompt_text}
        ],
        "temperature": 0.1,
        "max_tokens": 400
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            print(f"DeepSeek API error {resp.status_code}: {resp.text}")
            return None
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("DeepSeek call exception:", e)
        return None

# ---------- AI DECISION ----------
def ai_decision():
    # Top coins from CoinGecko
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=15&page=1"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            top_coins = resp.json()
            symbols = [COIN_MAP[coin['id']] for coin in top_coins if coin['id'] in COIN_MAP]
        else:
            raise ValueError("CoinGecko markets failed")
    except Exception as e:
        print(f"Fallback to default list: {e}")
        symbols = list(COIN_MAP.values())[:12]

    print(f"Gathering data for {len(symbols)} coins...")
    market_data = gather_market_data(symbols)

    if not market_data:
        return {"action": "HOLD", "reasoning": "No market data available"}

    prompt = f"""
You are "Crypto Institutional Desk – Multi‑Analysis", a paper trader on USDT perpetuals. Portfolio: {json.dumps(portfolio)}.

Real‑time market data (already fetched):
{json.dumps(market_data, indent=2)}

Analyse each coin (0‑2 points each):
1. Momentum & Microstructure: last vs vwap, orderbook imbalance, spread.
2. Positioning: long/short ratio (null = ignore), OI, funding rate.
3. Derivatives: funding rate neutrality.
4. Vol & Volume: ATR% ideal 1-8%, 24h vol > 500k USDT.
5. Catalyst: is_trending, 24h change, any general news.

Only trade if total score ≥ 7. Risk:
- risk = 5 USDT, stop = atr*1.8, qty = floor(5/stop) cap 150 USDT notional.
- STOP LOSS: entry ± stop. TAKE PROFIT: entry ± 2*stop (min). If score ≥8 and strong momentum, extend to 3:1 or 4:1. NEVER below 2:1.

OUTPUT ONLY JSON (no markdown):
{{"action":"LONG"|"SHORT"|"HOLD","symbol":"...","quantity":0.0,"order_type":"LIMIT","limit_price":0.0,"stop_loss":0.0,"take_profit":0.0,"confidence_score":0,"reasoning":"..."}}
If HOLD, omit numeric fields or set to 0 and explain.
"""
    print("Calling DeepSeek...")
    response = call_deepseek(prompt)
    if not response:
        return {"action": "HOLD", "reasoning": "DeepSeek API error"}

    try:
        text = response.strip()
        if "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text)
    except:
        print("DeepSeek raw:", response)
        return {"action": "HOLD", "reasoning": "JSON parse error"}

# ---------- TELEGRAM ----------
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("Telegram fail:", e)

def main():
    try:
        dec = ai_decision()
        msg = (f"📊 {dec.get('action','HOLD')} {dec.get('symbol','')}\n"
               f"Qty: {dec.get('quantity','')} | Score: {dec.get('confidence_score','')}\n"
               f"Stop: {dec.get('stop_loss','')} TP: {dec.get('take_profit','')}\n"
               f"Reason: {dec.get('reasoning','')}")
        print(msg)
        send_telegram(msg)
    except Exception as e:
        err_msg = f"Fatal: {traceback.format_exc()}"
        print(err_msg)
        send_telegram(err_msg[:500])

if __name__ == "__main__":
    main()
