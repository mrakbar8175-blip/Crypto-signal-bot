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

# ---------- EXPANDED COIN MAPPING (30+ altcoins) ----------
COIN_MAP = {
    "binancecoin": "BNBUSDT",
    "ripple": "XRPUSDT",
    "cardano": "ADAUSDT",
    "solana": "SOLUSDT",
    "dogecoin": "DOGEUSDT",
    "polkadot": "DOTUSDT",
    "uniswap": "UNIUSDT",
    "avalanche-2": "AVAXUSDT",
    "near": "NEARUSDT",
    "cosmos": "ATOMUSDT",
    "ethereum-classic": "ETCUSDT",
    "stellar": "XLMUSDT",
    "vechain": "VETUSDT",
    "filecoin": "FILUSDT",
    "aptos": "APTUSDT",
    "arbitrum": "ARBUSDT",
    "optimism": "OPUSDT",
    "injective-protocol": "INJUSDT",
    "celestia": "TIAUSDT",
    "sei-network": "SEIUSDT",
    "sui": "SUIUSDT",
    "thorchain": "RUNEUSDT",
    "the-graph": "GRTUSDT",
    "aave": "AAVEUSDT",
    "algorand": "ALGOUSDT",
    "the-sandbox": "SANDUSDT",
    "decentraland": "MANAUSDT",
    "theta-token": "THETAUSDT",
    "fantom": "FTMUSDT",
    "eos": "EOSUSDT",
    "maker": "MKRUSDT",
    "lido-dao": "LDOUSDT",
    "immutable-x": "IMXUSDT",
    "flow": "FLOWUSDT",
    "tezos": "XTZUSDT",
    "neo": "NEOUSDT",
    "kusama": "KSMUSDT",
    "zcash": "ZECUSDT",
    "dash": "DASHUSDT",
    "elrond-erd-2": "EGLDUSDT",
    "mina-protocol": "MINAUSDT",
    "gala": "GALAUSDT",
    "helium": "HNTUSDT",
    "conflux-token": "CFXUSDT",
    "arweave": "ARUSDT",
    "fetch-ai": "FETUSDT",
    "singularitynet": "AGIXUSDT",
    "ocean-protocol": "OCEANUSDT",
    "1inch": "1INCHUSDT",
    "curve-dao-token": "CRVUSDT",
}

# ---------- DATA HELPERS (swing version uses 4h klines) ----------
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

def get_swing_metrics(symbol, bid):
    klines = fetch_binance(f"/fapi/v1/klines?symbol={symbol}&interval=4h&limit=50")
    if isinstance(klines, list) and len(klines) >= 14:
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        volumes = [float(k[5]) for k in klines]

        cv, cvp = 0, 0
        for i in range(len(klines)):
            h, l, c = highs[i], lows[i], closes[i]
            v = volumes[i]
            tp = (h + l + c) / 3
            cvp += tp * v
            cv += v
        vwap = cvp / cv if cv else bid

        trs = []
        for i in range(len(klines)-14, len(klines)):
            h, l = highs[i], lows[i]
            prev_c = closes[i-1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        atr = sum(trs) / len(trs)

        sma20 = sum(closes[-20:]) / 20
        trend = "up" if closes[-1] > sma20 else "down"

        return {
            "vwap_4h": vwap,
            "atr_14_4h": atr,
            "sma20_4h": sma20,
            "trend_4h": trend,
            "current_price": closes[-1]
        }
    else:
        return {"vwap_4h": bid, "atr_14_4h": bid * 0.02, "sma20_4h": bid, "trend_4h": "neutral", "current_price": bid}

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
        print(f"Collecting swing data for {sym}...")
        fallback_price = price_map.get(sym)
        l2 = get_order_book_level2(sym, fallback_price)
        if not l2:
            continue
        swing = get_swing_metrics(sym, l2["bid"])
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
            "vwap_4h": swing["vwap_4h"],
            "atr_14_4h": swing["atr_14_4h"],
            "sma20_4h": swing["sma20_4h"],
            "trend_4h": swing["trend_4h"],
            "funding_rate": funding,
            "long_short_ratio": ls,
            "open_interest": oi,
            "24h_volume": vol24,
            "24h_change_pct": change24,
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
        "max_tokens": 800
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=45)
        if resp.status_code != 200:
            print(f"Groq error {resp.status_code}: {resp.text}")
            return None
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("Groq call exception:", e)
        return None

# ---------- AI DECISION ----------
def ai_decision():
    # Fetch top 100 coins from CoinGecko, filter to our universe, take top 30 by volume
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            all_coins = resp.json()
        else:
            raise ValueError("CoinGecko markets failed")
    except Exception as e:
        print(f"CoinGecko failed: {e}")
        fallback_ids = [k for k in COIN_MAP.keys() if k not in {"bitcoin", "ethereum", "chainlink", "litecoin", "matic-network"}][:30]
        all_coins = [{"id": k, "current_price": 0, "total_volume": 0, "price_change_percentage_24h": 0} for k in fallback_ids]

    excluded_ids = {"bitcoin", "ethereum", "chainlink", "litecoin", "matic-network"}
    candidates = [coin for coin in all_coins if coin["id"] not in excluded_ids and coin["id"] in COIN_MAP]
    candidates.sort(key=lambda x: x.get("total_volume", 0), reverse=True)
    top_coins = candidates[:30]

    symbols = []
    price_map = {}
    for coin in top_coins:
        sym = COIN_MAP[coin["id"]]
        symbols.append(sym)
        price_map[sym] = coin.get("current_price", 0)

    if not symbols:
        symbols = list(COIN_MAP.values())[:30]
        price_map = {s: 0 for s in symbols}

    print(f"Gathering swing data for {len(symbols)} altcoins...")
    market_data = gather_market_data(symbols, price_map)
    if not market_data:
        return {"action": "HOLD", "reasoning": "No market data available"}

    # ---------- ROCK‑SOLID SWING PROMPT (score ≥ 7) ----------
    prompt = f"""
You are "Crypto Institutional Desk – Swing Trader". You trade USDT perpetuals on a 1000 USDT paper account with a swing trading approach (holding periods of hours to a few days). Your analysis is based ONLY on the real data provided below. Do NOT invent any numbers.

Universe: 30 most liquid altcoins (BTC, ETH, LINK, LTC, MATIC excluded).

Current portfolio: {json.dumps(portfolio)}

Real‑time Level 2 and 4‑hour chart data:
{json.dumps(market_data, indent=2)}

**Swing Trading Strategy:**
- Look for coins in established 4‑hour trends (trend_4h = "up" or "down") with a retracement or breakout that aligns with the trend.
- Entry should be near a key level (VWAP, SMA20) with order‑book confirmation (imbalance_10 > 0.3 for longs, < -0.3 for shorts).
- Swing trades require a wider stop to avoid noise; use ATR_14_4h (already calculated) for volatility.
- Favor coins with rising volume on the 4h candle and neutral funding rates.
- Only take a trade if the 4h trend, order book, and volume all agree.

**Scoring – assign 0‑2 points each (max 12):**
1. **Trend Alignment (0‑2):** 
   - 2 points: trend_4h is strongly up/down AND price recently bounced off VWAP/SMA20.
   - 1 point: trend is up/down but price extended from moving averages.
   - 0 points: no clear trend or trend is neutral.
2. **Order‑Book Confirmation (0‑2):**
   - 2 points: imbalance_10 > 0.5 (long) or < -0.5 (short) AND bid/ask walls strongly support the direction.
   - 1 point: moderate imbalance.
   - 0 points: weak or contradictory.
3. **Positioning & Funding (0‑2):**
   - 2 points: funding_rate between -0.05% and 0.05% AND long_short_ratio not excessively skewed (1.5‑2.5 for longs, 0.5‑1.5 for shorts, or null accepted).
   - 1 point: mixed.
   - 0 points: extreme funding or heavily one‑sided positioning.
4. **Volume & Volatility (0‑2):**
   - 2 points: 24h_volume > 1M USDT AND ATR_14_4h between 2% and 8% of price.
   - 1 point: borderline.
   - 0 points: too volatile or low volume.
5. **Catalyst (0‑2):**
   - 2 points: is_trending = True AND 24h_change_pct > 3% (if long) or < -3% (if short).
   - 1 point: one condition true.
   - 0 points: no catalyst.
6. **Swing Confluence (0‑2):**
   - 2 points: at least 4 of the above layers score ≥1, and overall setup shows a clear swing entry.
   - 1 point: somewhat mixed.
   - 0 points: no confluence.

**Trade rules:**
- Only trade if **total score ≥ 7** AND 4h trend and order‑book imbalance strongly support the direction.
- **confident_score** = total points (max 12). Do not inflate. A score of 9‑10 is extremely rare.
- If no coin reaches 7, action = HOLD.

**Risk management (SWING):**
- risk = 5 USDT (0.5% of portfolio)
- stop distance = atr_14_4h * 1.5   (using 4‑hour ATR)
- quantity = floor(5 / stop_distance), capped at 150 USDT notional
- STOP LOSS: entry ± stop_distance
- TAKE PROFIT: entry ± 2 * stop_distance (MINIMUM). If score ≥ 9 and trend is extremely strong, you may extend to 3:1. **Always verify your math; TP can never be closer than 2:1.**

**Output ONLY a JSON object (no markdown):**
{{"action":"LONG"|"SHORT"|"HOLD","symbol":"BNBUSDT","quantity":0.0,"order_type":"LIMIT","limit_price":0.0,"stop_loss":0.0,"take_profit":0.0,"confidence_score":0,"reasoning":"L1:X L2:Y ... explanation"}}
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
        decision = json.loads(text)
    except:
        print("Raw Groq response:", response)
        return {"action": "HOLD", "reasoning": "JSON parse error"}

    # ---------- AUTO-CORRECT RR (must be ≥ 2:1) ----------
    action = decision.get("action")
    if action in ("LONG", "SHORT"):
        entry = float(decision.get("limit_price", 0))
        stop = float(decision.get("stop_loss", 0))
        tp = float(decision.get("take_profit", 0))

        if entry <= 0 or stop <= 0 or tp <= 0:
            return {"action": "HOLD", "reasoning": "Invalid price values from AI"}

        if action == "LONG":
            risk = entry - stop
            if risk <= 0:
                return {"action": "HOLD", "reasoning": "Stop loss above entry for LONG"}
            min_tp = entry + 2 * risk
            if tp < min_tp:
                print(f"Correcting TP from {tp} to {min_tp} (2:1 RR)")
                decision["take_profit"] = round(min_tp, 6)
                decision["reasoning"] += " | TP auto-corrected to enforce 2:1 minimum RR"
        else:  # SHORT
            risk = stop - entry
            if risk <= 0:
                return {"action": "HOLD", "reasoning": "Stop loss below entry for SHORT"}
            min_tp = entry - 2 * risk
            if tp > min_tp:
                print(f"Correcting TP from {tp} to {min_tp} (2:1 RR)")
                decision["take_profit"] = round(min_tp, 6)
                decision["reasoning"] += " | TP auto-corrected to enforce 2:1 minimum RR"

    # ---------- SANITY CHECK ON CONFIDENCE SCORE ----------
    try:
        raw_score = int(decision.get("confidence_score", 0))
        sym = decision.get("symbol")
        coin_data = next((c for c in market_data if c["symbol"] == sym), None)
        if coin_data:
            spread = coin_data.get("spread_pct", 0)
            imbalance = abs(coin_data.get("imbalance_10", 0))
            vol = coin_data.get("24h_volume", 0)
            atr_pct = (coin_data.get("atr_14_4h", 0) / coin_data.get("bid", 1)) * 100

            if spread > 0.1:
                raw_score = min(raw_score, 6)
            if vol < 500000:
                raw_score = min(raw_score, 5)
            if imbalance < 0.2:
                raw_score = min(raw_score, 6)
            if atr_pct > 12 or atr_pct < 1:
                raw_score = min(raw_score, 5)
            decision["confidence_score"] = raw_score
    except:
        pass

    return decision

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

        msg = (f"📊 SWING {dec.get('action','HOLD')} {dec.get('symbol','')}\n"
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
