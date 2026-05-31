import requests, json, os, time, traceback
from pycoingecko import CoinGeckoAPI

# ---------- ENVIRONMENT ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

cg = CoinGeckoAPI()

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

# ---------- SAFE BINANCE FETCH ----------
def safe_get(dictionary, *keys, default=None):
    """Traverse nested dicts safely."""
    for key in keys:
        if isinstance(dictionary, dict) and key in dictionary:
            dictionary = dictionary[key]
        else:
            return default
    return dictionary

def fetch_binance(endpoint):
    try:
        r = requests.get("https://fapi.binance.com" + endpoint, timeout=10)
        return r.json()
    except:
        return {"error": "request failed"}

def get_order_book_data(symbol):
    """Return a dict with bid, ask, spread, imbalance, vwap, atr, volume. Uses CoinGecko as ultimate fallback."""
    # Try Binance depth
    depth = fetch_binance(f"/fapi/v1/depth?symbol={symbol}&limit=5")
    if "bids" in depth and "asks" in depth:
        bids = depth["bids"]
        asks = depth["asks"]
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        spread_pct = (ask - bid) / ask * 100
        bid_vol = sum(float(b[1]) for b in bids[:3])
        ask_vol = sum(float(a[1]) for a in asks[:3])
        total = bid_vol + ask_vol
        imbalance = (bid_vol - ask_vol) / total if total else 0
    else:
        # Fallback to CoinGecko
        coin_id = [k for k, v in COIN_MAP.items() if v == symbol][0]
        price = get_cg_price(coin_id)
        if price == 0:
            return None
        bid = price * 0.9999
        ask = price * 1.0001
        spread_pct = 0.02
        imbalance = 0

    # ATR & VWAP from klines
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

    # Volume from ticker
    ticker = fetch_binance(f"/fapi/v1/ticker/24hr?symbol={symbol}")
    if "symbol" in ticker:
        volume = float(ticker.get("quoteVolume", 0))
        change = float(ticker.get("priceChangePercent", 0))
    else:
        volume = 1000000
        change = 0

    return {
        "bid": bid,
        "ask": ask,
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
    return 0.01  # neutral

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
        trending = cg.get_search_trending()
        for item in trending.get("coins", []):
            if item["item"]["id"] == coin_id:
                return True
    except:
        pass
    return False

def get_coin_data(symbol):
    coin_id = [k for k, v in COIN_MAP.items() if v == symbol][0]
    base = get_order_book_data(symbol)
    if base is None:
        return None  # price unavailable
    base["symbol"] = symbol
    base["funding_rate"] = get_funding(symbol)
    base["long_short_ratio"] = get_ls_ratio(symbol)
    base["open_interest"] = get_oi(symbol)
    base["is_trending"] = get_trending(coin_id)
    return base

def gather_market_data(symbols):
    results = []
    for sym in symbols:
        try:
            info = get_coin_data(sym)
            if info:
                results.append(info)
        except Exception as e:
            print(f"Skipping {sym} due to error: {e}")
    return results

# ---------- GEMINI REST API ----------
def call_gemini(prompt_text):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 400}
    }
    try:
        resp = requests.post(url, headers=headers, params={"key": GEMINI_API_KEY}, json=body, timeout=30)
        if resp.status_code != 200:
            print(f"Gemini API error {resp.status_code}: {resp.text}")
            return None
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print("Gemini call exception:", e)
        return None

# ---------- AI DECISION ----------
def ai_decision():
    # Get top symbols from CoinGecko
    try:
        top_coins = cg.get_coins_markets(vs_currency='usd', order='volume_desc', per_page=15, page=1)
        symbols = [COIN_MAP[coin['id']] for coin in top_coins if coin['id'] in COIN_MAP]
        if not symbols:
            raise ValueError("No matching coins")
    except Exception as e:
        print("Fallback to default list:", e)
        symbols = list(COIN_MAP.values())[:12]

    print(f"Fetching data for {len(symbols)} coins...")
    market_data = gather_market_data(symbols)

    if not market_data:
        return {"action": "HOLD", "reasoning": "No market data available for any coin"}

    prompt = f"""
You are "Crypto Institutional Desk – Multi‑Analysis", a paper trader on USDT perpetuals. Portfolio: {json.dumps(portfolio)}.

Real‑time data for the most liquid coins (already fetched, no need to call any function):
{json.dumps(market_data, indent=2)}

Analyse each coin using these layers (0‑2 points each):
1. Momentum & Microstructure: last price vs VWAP, orderbook imbalance, spread.
2. Positioning: long/short ratio (if not null), open interest, funding rate.
3. Derivatives: funding rate neutrality, OI changes (assume flat if not provided).
4. Volatility & Volume: ATR% of price (ideal 1–8%), 24h volume > 500k USDT.
5. Catalyst / Sentiment: is_trending, 24h change, and any general market news you know.

Only output a trade if total score ≥ 7. Risk management:
- risk = 5 USDT (0.5% of 1000)
- stop distance = atr_14 * 1.8
- quantity = floor(5 / stop distance), capped so that position notional ≤ 150 USDT
- STOP LOSS: entry ± stop distance
- TAKE PROFIT: entry ± 2× stop distance minimum. If score ≥ 8 and strong momentum, extend to 3:1 or 4:1. NEVER below 2:1.

OUTPUT ONLY A JSON (no other text):
{{"action":"LONG"|"SHORT"|"HOLD","symbol":"BTCUSDT","quantity":0.01,"order_type":"LIMIT","limit_price":70000,"stop_loss":69800,"take_profit":70400,"confidence_score":8,"reasoning":"..."}}
If no trade qualifies, use action "HOLD" and explain briefly.
"""
    print("Calling Gemini...")
    response = call_gemini(prompt)
    if not response:
        return {"action": "HOLD", "reasoning": "Gemini API error"}

    try:
        text = response.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]
        return json.loads(text)
    except:
        print("Failed to parse Gemini response:", response)
        return {"action": "HOLD", "reasoning": "JSON parse error"}

# ---------- TELEGRAM ----------
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
        print("Telegram status:", resp.status_code)
    except Exception as e:
        print("Telegram send failed:", e)

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
        err_msg = f"Bot crashed: {traceback.format_exc()}"
        print(err_msg)
        send_telegram(err_msg[:500])

if __name__ == "__main__":
    main()
