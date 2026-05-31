import requests, json, os, traceback

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
    "daily_loss_limit": -50
}

# ---------- COIN MAPPING (10 altcoins universe; no BTC,ETH,LINK,LTC,MATIC) ----------
COIN_MAP = {
    "binancecoin": "BNBUSDT",
    "ripple": "XRPUSDT",
    "cardano": "ADAUSDT",
    "solana": "SOLUSDT",
    "dogecoin": "DOGEUSDT",
    "polkadot": "DOTUSDT",
    "uniswap": "UNIUSDT",
    "avalanche-2": "AVAXUSDT",
    "near": "NEARUSDT",               # replaced MATIC
    "cosmos": "ATOMUSDT",
    # Additional coins are available but the bot only scans the top 10 by volume
}

# ---------- DATA HELPERS ----------
def fetch_binance(endpoint):
    try:
        r = requests.get("https://fapi.binance.com" + endpoint, timeout=10)
        return r.json()
    except:
        return {"error": "request failed"}

def get_order_book_level2(symbol, fallback_price=None):
    depth = fetch_binance(f"/fapi/v1/depth?symbol={symbol}&limit=10")
    if "bids" in depth and "asks" in depth:
        bids = depth["bids"]
        asks = depth["asks"]
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        spread_pct = (ask - bid) / ask * 100
        bid_vol_10 = sum(float(b[1]) for b in bids[:10])
        ask_vol_10 = sum(float(a[1]) for a in asks[:10])
        total_vol_10 = bid_vol_10 + ask_vol_10
        imbalance_10 = (bid_vol_10 - ask_vol_10) / total_vol_10 if total_vol_10 else 0
        bid_wall = max(bids[:10], key=lambda x: float(x[1]))
        ask_wall = max(asks[:10], key=lambda x: float(x[1]))
        mid = (bid + ask) / 2
        bid_vol_1pct = sum(float(b[1]) for b in bids if float(b[0]) >= mid * 0.99)
        ask_vol_1pct = sum(float(a[1]) for a in asks if float(a[0]) <= mid * 1.01)
        total_1pct = bid_vol_1pct + ask_vol_1pct
        imbalance_1pct = (bid_vol_1pct - ask_vol_1pct) / total_1pct if total_1pct else 0
        return {
            "bid": bid, "ask": ask, "spread_pct": spread_pct,
            "imbalance_10": imbalance_10, "imbalance_1pct": imbalance_1pct,
            "bid_wall_price": float(bid_wall[0]), "bid_wall_volume": float(bid_wall[1]),
            "ask_wall_price": float(ask_wall[0]), "ask_wall_volume": float(ask_wall[1]),
            "bid_depth_10": bid_vol_10, "ask_depth_10": ask_vol_10
        }

    if fallback_price and fallback_price > 0:
        bid = fallback_price * 0.9999
        ask = fallback_price * 1.0001
        return {
            "bid": bid, "ask": ask, "spread_pct": 0.02,
            "imbalance_10": 0, "imbalance_1pct": 0,
            "bid_wall_price": bid, "bid_wall_volume": 1,
            "ask_wall_price": ask, "ask_wall_volume": 1,
            "bid_depth_10": 1, "ask_depth_10": 1
        }
    return None

def get_volatility_atr(symbol, bid):
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
    return vwap, atr

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

def gather_market_data(symbols, price_map):
    results = []
    for sym in symbols:
        print(f"Collecting L2 data for {sym}...")
        fallback_price = price_map.get(sym)
        l2 = get_order_book_level2(sym, fallback_price)
        if not l2:
            continue
        vwap, atr = get_volatility_atr(sym, l2["bid"])
        vol24, change24 = get_24h_metrics(sym)
        funding = get_funding(sym)
        ls = get_ls_ratio(sym)
        oi = get_oi(sym)
        coin_id = [k for k, v in COIN_MAP.items() if v == sym][0]
        trending = get_trending(coin_id)
        data = {
            "symbol": sym,
            "bid": l2["bid"], "ask": l2["ask"],
            "spread_pct": l2["spread_pct"],
            "imbalance_10": l2["imbalance_10"],
            "imbalance_1pct": l2["imbalance_1pct"],
            "bid_wall_price": l2["bid_wall_price"],
            "bid_wall_volume": l2["bid_wall_volume"],
            "ask_wall_price": l2["ask_wall_price"],
            "ask_wall_volume": l2["ask_wall_volume"],
            "bid_depth_10": l2["bid_depth_10"],
            "ask_depth_10": l2["ask_depth_10"],
            "vwap_1h": vwap, "atr_14": atr,
            "funding_rate": funding,
            "long_short_ratio": ls,
            "open_interest": oi,
            "24h_volume": vol24, "24h_change_pct": change24,
            "is_trending": trending
        }
        results.append(data)
    return results

# ---------- GROQ API CALL ----------
def call_groq(prompt_text):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": 0.1,
        "max_tokens": 500
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            print(f"Groq error {resp.status_code}: {resp.text}")
            return None
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("Groq call exception:", e)
        return None

# ---------- AI DECISION ----------
def ai_decision():
    # Get top coins from CoinGecko, EXCLUDE: bitcoin, ethereum, chainlink, litecoin, matic-network
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=30&page=1"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            all_coins = resp.json()
            excluded_ids = {"bitcoin", "ethereum", "chainlink", "litecoin", "matic-network"}
            filtered = [coin for coin in all_coins if coin["id"] not in excluded_ids]
            candidates = []
            for coin in filtered:
                if coin["id"] in COIN_MAP:
                    candidates.append(coin)
                if len(candidates) >= 10:
                    break
            top_coins = candidates
        else:
            raise ValueError("CoinGecko markets failed")
    except Exception as e:
        print(f"CoinGecko failed: {e}")
        # Fallback: use first 10 altcoins from our map (excluding the five)
        fallback_ids = [k for k in COIN_MAP.keys() if k not in {"bitcoin", "ethereum", "chainlink", "litecoin", "matic-network"}][:10]
        top_coins = [{"id": k, "current_price": 0, "total_volume": 0, "price_change_percentage_24h": 0}
                     for k in fallback_ids]

    symbols = []
    price_map = {}
    for coin in top_coins:
        sym = COIN_MAP[coin["id"]]
        symbols.append(sym)
        price_map[sym] = coin.get("current_price", 0)

    if not symbols:
        symbols = [s for s in list(COIN_MAP.values())[:10] if s not in ["LINKUSDT", "LTCUSDT", "MATICUSDT"]]
        price_map = {s: 0 for s in symbols}

    print(f"Gathering Level2+ data for {len(symbols)} altcoins (excl BTC,ETH,LINK,LTC,MATIC): {symbols}")
    market_data = gather_market_data(symbols, price_map)
    if not market_data:
        return {"action": "HOLD", "reasoning": "No market data available"}

    prompt = f"""
You are "Crypto Institutional Desk – Multi‑Analysis". You trade USDT perpetuals on a 1000 USDT paper account.
(BTC, ETH, LINK, LTC, MATIC are excluded. Universe includes NEAR and other liquid altcoins.)

Current portfolio: {json.dumps(portfolio)}

Real‑time Level 2 market data for the top 10 altcoins by volume:
{json.dumps(market_data, indent=2)}

Analyse each coin using these 6 layers (0‑2 points each, max 12):
1. **Order‑Book Depth & Walls** – imbalance_10, imbalance_1pct, bid/ask wall sizes.
2. **Momentum & Microstructure** – bid vs vwap_1h, spread, 24h_change_pct.
3. **Positioning** – long_short_ratio (null = ignore), open_interest, funding_rate.
4. **Volatility & Volume** – atr_14% of price (ideal 1‑8%), 24h_volume > 500k USDT.
5. **Catalyst / Sentiment** – is_trending, 24h_change_pct magnitude, any news you know.
6. **Risk/Reward & Confluence** – only high‑confidence setups.

Only issue a trade if **total score ≥ 7** AND the order‑book imbalance clearly supports the direction.

**Risk management:**
- risk = 5 USDT (0.5% of portfolio)
- stop distance = atr_14 * 1.8
- quantity = floor(5 / stop_distance), capped at 150 USDT notional
- STOP LOSS: entry ± stop_distance
- TAKE PROFIT: entry ± 2 * stop_distance (minimum). If score ≥ 9 and walls are massive, extend to 3:1 or 4:1. NEVER below 2:1.

**Output ONLY a JSON (no markdown):**
{{"action":"LONG"|"SHORT"|"HOLD","symbol":"BNBUSDT","quantity":0.0,"order_type":"LIMIT","limit_price":0.0,"stop_loss":0.0,"take_profit":0.0,"confidence_score":0,"reasoning":"..."}}
If HOLD, omit numeric fields or set to 0 and explain briefly.
"""
    print("Calling Groq...")
    response = call_groq(prompt)
    if not response:
        return {"action": "HOLD", "reasoning": "Groq API error"}

    try:
        text = response.strip()
        if "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text)
    except:
        print("Raw Groq response:", response)
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
        if dec.get("action") in ["LONG", "SHORT"]:
            entry_price = dec.get("limit_price", 0)
            current_price_line = f"Current price (bid/ask): {entry_price}\n"
        else:
            current_price_line = ""

        msg = (f"📊 {dec.get('action','HOLD')} {dec.get('symbol','')}\n"
               f"{current_price_line}"
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
