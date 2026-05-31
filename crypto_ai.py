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

# ---------- DATA FETCHING HELPERS ----------
def fetch_binance(endpoint):
    try:
        r = requests.get("https://fapi.binance.com" + endpoint, timeout=10)
        return r.json()
    except:
        return {"error": "request failed"}

def get_cg_price(coin_id):
    try:
        data = cg.get_price(ids=coin_id, vs_currencies='usd')
        return data[coin_id]['usd']
    except:
        return 0

def get_coin_data(symbol):
    """Gather all available data for a single coin."""
    coin_id = [k for k, v in COIN_MAP.items() if v == symbol][0]
    data = {"symbol": symbol}

    # 1. Order book from Binance (or approximate via CoinGecko)
    depth = fetch_binance(f"/fapi/v1/depth?symbol={symbol}&limit=5")
    if "error" not in depth and "bids" in depth:
        bids = depth["bids"]
        asks = depth["asks"]
        data["bid"] = float(bids[0][0])
        data["ask"] = float(asks[0][0])
        data["spread_pct"] = (data["ask"] - data["bid"]) / data["ask"] * 100
        bid_vol = sum(float(b[1]) for b in bids[:3])
        ask_vol = sum(float(a[1]) for a in asks[:3])
        total = bid_vol + ask_vol
        data["orderbook_imbalance"] = (bid_vol - ask_vol) / total if total else 0
    else:
        price = get_cg_price(coin_id)
        if price == 0:
            data["bid"] = data["ask"] = 0
            data["spread_pct"] = 0.02
            data["orderbook_imbalance"] = 0
        else:
            data["bid"] = price * 0.9999
            data["ask"] = price * 1.0001
            data["spread_pct"] = 0.02
            data["orderbook_imbalance"] = 0

    # 2. ATR & VWAP from Binance klines (or approximation)
    klines = fetch_binance(f"/fapi/v1/klines?symbol={symbol}&interval=1m&limit=60")
    if "error" not in klines and len(klines) >= 60:
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
        data["vwap_1h"] = cvp / cv if cv else data["bid"]
        data["atr_14"] = sum(trs) / len(trs)
    else:
        data["vwap_1h"] = data["bid"]
        data["atr_14"] = data["bid"] * 0.015  # approx 1.5%

    # 3. Funding rate, long/short ratio, OI from Binance
    fr = fetch_binance(f"/fapi/v1/premiumIndex?symbol={symbol}")
    if "error" not in fr:
        data["funding_rate"] = float(fr["lastFundingRate"]) * 100
    else:
        data["funding_rate"] = 0.01  # neutral

    ls = fetch_binance(f"/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m")
    if "error" not in ls and ls:
        data["long_short_ratio"] = float(ls[0]["longShortRatio"])
    else:
        data["long_short_ratio"] = None

    oi = fetch_binance(f"/fapi/v1/openInterest?symbol={symbol}")
    if "error" not in oi:
        data["open_interest"] = float(oi["openInterest"])
    else:
        data["open_interest"] = 0

    # 4. 24h change & volume (Binance ticker)
    ticker = fetch_binance(f"/fapi/v1/ticker/24hr?symbol={symbol}")
    if "error" not in ticker:
        data["24h_change_pct"] = float(ticker.get("priceChangePercent", 0))
        data["24h_volume"] = float(ticker.get("quoteVolume", 0))
    else:
        # Fallback to CoinGecko
        try:
            coin = cg.get_coin_by_id(coin_id)
            data["24h_change_pct"] = coin["market_data"]["price_change_percentage_24h"]
            data["24h_volume"] = coin["market_data"]["total_volume"]["usd"]
        except:
            data["24h_change_pct"] = 0
            data["24h_volume"] = 0

    # 5. CoinGecko social / trending (optional)
    try:
        trending_coins = cg.get_search_trending()
        trending_names = [trend["item"]["id"] for trend in trending_coins.get("coins", [])]
        data["is_trending"] = coin_id in trending_names
    except:
        data["is_trending"] = False

    return data

def gather_market_data(top_symbols):
    """Collect data for a list of symbols, return a list of data dicts."""
    results = []
    for sym in top_symbols:
        try:
            info = get_coin_data(sym)
            if info["bid"] > 0:   # minimal check
                results.append(info)
        except Exception as e:
            print(f"Error fetching {sym}: {e}")
    return results

# ---------- GEMINI REST API CALL ----------
def call_gemini(prompt_text):
    """Send prompt to Gemini 2.0 Flash via REST API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{
            "parts": [{"text": prompt_text}]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 400
        }
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code != 200:
            print("Gemini API error:", resp.status_code, resp.text)
            return None
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print("Gemini call failed:", e)
        return None

# ---------- MAIN AI DECISION ----------
def ai_decision():
    # Get top coins by volume from CoinGecko
    try:
        top_coins = cg.get_coins_markets(vs_currency='usd', order='volume_desc', per_page=15, page=1)
        symbols = []
        for coin in top_coins:
            sym = COIN_MAP.get(coin['id'])
            if sym:
                symbols.append(sym)
        if not symbols:
            raise ValueError("No matching coins")
    except Exception as e:
        print("CoinGecko fallback, using default list:", e)
        symbols = list(COIN_MAP.values())[:15]

    print(f"Fetching data for {len(symbols)} coins...")
    market_data = gather_market_data(symbols)

    # Build a detailed prompt for Gemini
    prompt = f"""
You are "Crypto Institutional Desk – Multi‑Analysis", a paper trader on USDT perpetuals. Portfolio: {json.dumps(portfolio)}.

Real‑time data for the most liquid coins (already fetched):
{json.dumps(market_data, indent=2)}

Analyse each coin using these layers (0‑2 points each):
1. Momentum & Microstructure: last price vs VWAP, orderbook imbalance, spread.
2. Positioning: long/short ratio (if available), open interest, funding rate.
3. Derivatives: recent liquidations (inferred from OI/price), funding rate neutrality.
4. Volatility & Volume: ATR% of price (1‑8% ideal), 24h volume > 500k USDT.
5. Catalyst / Sentiment: is_trending, 24h change magnitude, news context (use your knowledge).

Only output a trade if total score ≥ 7. Use strict risk:
- risk = 5 USDT (0.5% of 1,000)
- stop distance = atr_14 * 1.8
- quantity = floor(5 / stop_distance), cap at 15% of balance notional.
- STOP LOSS: entry ± stop distance.
- TAKE PROFIT: entry ± 2× stop distance minimum. If score ≥ 8 and strong momentum, you may extend to 3:1 or 4:1. NEVER go below 2:1.

OUTPUT ONLY A JSON (no other text):
{{"action":"LONG"|"SHORT"|"HOLD","symbol":"BTCUSDT","quantity":0.01,"order_type":"LIMIT","limit_price":70000,"stop_loss":69800,"take_profit":70400,"confidence_score":8,"reasoning":"..."}}
If no coin qualifies, use action "HOLD" with reasoning.
"""
    print("Calling Gemini...")
    response = call_gemini(prompt)
    if not response:
        return {"action":"HOLD","reasoning":"Gemini API error"}

    # Parse the JSON response
    try:
        # Extract JSON from possible code blocks
        text = response.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]
        decision = json.loads(text)
        return decision
    except:
        print("Failed to parse Gemini response:", response)
        return {"action":"HOLD","reasoning":"JSON parse error"}

# ---------- TELEGRAM SEND ----------
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
        print("Telegram status:", resp.status_code, resp.text[:100])
    except Exception as e:
        print("Telegram send failed:", e)

# ---------- MAIN ----------
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
        err_msg = f"Bot error: {traceback.format_exc()}"
        print(err_msg)
        send_telegram(err_msg[:500])

if __name__ == "__main__":
    main()
