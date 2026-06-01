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
    "daily_loss_limit": -20   # 2% of 1000
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

def ichimoku(highs, lows):
    if len(highs)<26: return None
    tenkan = (max(highs[-9:]) + min(lows[-9:]))/2
    kijun = (max(highs[-26:]) + min(lows[-26:]))/2
    return {"tenkan": tenkan, "kijun": kijun}

def get_multi_tf_analysis(symbol):
    data = {}
    for interval, limit in [("4h", 50), ("1h", 50), ("15m", 50)]:
        klines = fetch_binance(f"/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}")
        if isinstance(klines, list) and len(klines) >= limit:
            opens = [float(k[1]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            rsi = calculate_rsi(closes)
            ema50 = compute_ema(closes, 50) if len(closes)>=50 else closes[-1]
            ema200 = compute_ema(closes, 200) if len(closes)>=200 else closes[-1]
            ichi = ichimoku(highs, lows) if interval == "1h" else None
            trend = "up" if closes[-1] > ema50 else "down"
            data[interval] = {
                "close": closes[-1],
                "rsi": rsi,
                "ema50": ema50,
                "ema200": ema200,
                "trend": trend,
                "ichi": ichi
            }
        else:
            data[interval] = None
    return data

def get_volume_profile_approx(symbol, bid, ask):
    depth = fetch_binance(f"/fapi/v1/depth?symbol={symbol}&limit=20")
    if "bids" in depth and "asks" in depth:
        bids = depth["bids"]
        asks = depth["asks"]
        all_levels = [(float(p), float(q)) for p,q in bids] + [(float(p), float(q)) for p,q in asks]
        all_levels.sort(key=lambda x: x[0])
        if all_levels:
            poc = max(all_levels, key=lambda x: x[1])[0]
            return {"poc": poc, "vah": ask, "val": bid}
    return {"poc": bid, "vah": ask, "val": bid}

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
        vp = get_volume_profile_approx(symbol, bid, ask)
        return {
            "bid": bid, "ask": ask, "spread_pct": spread_pct,
            "imbalance_10": imbalance_10, "imbalance_1pct": imbalance_1pct,
            "bid_wall_price": float(bid_wall[0]), "bid_wall_volume": float(bid_wall[1]),
            "ask_wall_price": float(ask_wall[0]), "ask_wall_volume": float(ask_wall[1]),
            "bid_depth_10": bid_vol_10, "ask_depth_10": ask_vol_10,
            "volume_profile": vp
        }

    if fallback_price and fallback_price > 0:
        bid = fallback_price * 0.9999
        ask = fallback_price * 1.0001
        return {
            "bid": bid, "ask": ask, "spread_pct": 0.02,
            "imbalance_10": 0, "imbalance_1pct": 0,
            "bid_wall_price": bid, "bid_wall_volume": 1,
            "ask_wall_price": ask, "ask_wall_volume": 1,
            "bid_depth_10": 1, "ask_depth_10": 1,
            "volume_profile": {"poc": bid, "vah": ask, "val": bid}
        }
    return None

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

def get_macro_data():
    data = fetch_coingecko("https://api.coingecko.com/api/v3/global")
    if data:
        total_mcap = data["data"]["total_market_cap"]["usd"]
        btc_mcap = data["data"]["market_cap_percentage"]["btc"]
        eth_mcap = data["data"]["market_cap_percentage"]["eth"]
        others = data["data"]["market_cap_percentage"].get("others", 10)
        total2 = total_mcap * (1 - btc_mcap/100)
        total3 = total_mcap * (others/100)
        btc_d = btc_mcap
        usdt_d = data["data"]["market_cap_percentage"].get("usdt", 3)
        dxy = 104.5   # placeholder
        return {
            "total_mcap": total_mcap,
            "total2": total2,
            "total3": total3,
            "btc_d": btc_d,
            "usdt_d": usdt_d,
            "dxy": dxy
        }
    return None

def gather_market_data(symbols, price_map):
    macro = get_macro_data()
    results = []
    for sym in symbols:
        print(f"Enhancing data for {sym}...")
        fallback_price = price_map.get(sym)
        l2 = get_order_book_level2(sym, fallback_price)
        if not l2:
            continue
        multi_tf = get_multi_tf_analysis(sym)
        funding = get_funding(sym)
        ls = get_ls_ratio(sym)
        oi = get_oi(sym)
        vol24, change24 = get_24h_metrics(sym)
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
            "volume_profile": l2["volume_profile"],
            "multi_tf": multi_tf,
            "funding_rate": funding,
            "long_short_ratio": ls,
            "open_interest": oi,
            "24h_volume": vol24,
            "24h_change_pct": change24,
            "is_trending": trending
        }
        results.append(data)
    return results, macro

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
        "max_tokens": 1000
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

# ---------- ROBUST JSON EXTRACTION ----------
def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 3:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        return text[start:end+1]
    return text

# ---------- AI DECISION ----------
def ai_decision():
    # Fetch top 30 coins (excluded list)
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            all_coins = resp.json()
        else:
            raise ValueError("CoinGecko markets failed")
    except Exception as e:
        print(f"CoinGecko failed: {e}")
        all_coins = [{"id": k, "current_price": 0, "total_volume": 0, "price_change_percentage_24h": 0}
                     for k in COIN_MAP if k not in {"bitcoin", "ethereum", "chainlink", "litecoin", "matic-network"}][:30]

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

    print(f"Gathering institutional data for {len(symbols)} altcoins...")
    market_data, macro = gather_market_data(symbols, price_map)
    if not market_data:
        return {"action": "HOLD", "reasoning": "No market data available"}

    # ---------- INSTITUTIONAL PROMPT ----------
    prompt = f"""
You are the head of quantitative research at a crypto prop trading firm. Analyze the following real-time data for 30 altcoins and produce ONE trade signal.

**MACRO CONTEXT:**
{json.dumps(macro, indent=2)}

**DETAILED ALTCOIN DATA:**
{json.dumps(market_data, indent=2)}

**INSTRUCTIONS:**
1. For each coin, compute a weighted conviction score from -3 (strong short) to +3 (strong long) using these layers:
   - Technical (multi-TF: 4h,1h,15m trend, RSI, EMAs, Ichimoku) – weight 25%
   - Volume Profile (POC, VAH, VAL, walls) – weight 15%
   - Order Flow (order book imbalance, CVD proxy) – weight 15%
   - Derivatives (funding, OI, LS ratio) – weight 10%
   - Volume & Momentum (24h vol, change) – weight 10%
   - Sentiment (is_trending) – weight 5%
   - Macro Confluence (TOTAL1,2,3, DXY, BTC.D) – weight 20%
   Aggregate by weighted average.

2. The highest absolute conviction score must be **≥ 1.5** (which translates to a confidence score of 6 or higher) to trigger a trade. If the best coin falls below this threshold, or if macro strongly contradicts, output HOLD.

3. Map conviction to confidence 1-10 exactly as:
   Conviction ≥ 2.5 → Confidence 9-10
   Conviction 2.0–2.4 → Confidence 7-8
   Conviction 1.5–1.9 → Confidence 5-6
   Below 1.5 → NO TRADE

4. For the chosen coin, set entry at current bid (long) or ask (short), or use a LIMIT order at a high-probability level (POC, VAL/VAH, recent swing) if better. Stop-loss beyond a logical swing level or 1.5× ATR, ensuring risk ≤ 1% of virtual account (10 USDT). Position size = 10 / (stop distance in USDT). Six take-profits:
   - TP1 = 0.2R, TP2 = 0.4R, TP3 = 0.8R, TP4 = 1.2R, TP5 = 1.6R, TP6 ≥ 2.5R
   Place TPs at the nearest logical level (round numbers, volume profile nodes, prior highs/lows).
   Scale: 20% | 20% | 20% | 15% | 15% | 10%

5. Output ONLY a clean JSON (no markdown, no extra text). The JSON must be exactly in this format:

{{"action":"LONG","symbol":"BNBUSDT","quantity":0.0,"order_type":"LIMIT","limit_price":0.0,"stop_loss":0.0,"take_profit_1":0.0,"take_profit_2":0.0,"take_profit_3":0.0,"take_profit_4":0.0,"take_profit_5":0.0,"take_profit_6":0.0,"confidence_score":6,"reasoning":"..."}}

If HOLD, output: {{"action":"HOLD","reasoning":"..."}}
"""
    print("Calling Groq...")
    response = call_groq(prompt)
    if not response:
        return {"action": "HOLD", "reasoning": "Groq API error"}

    # ---------- PARSE WITH RETRY ----------
    decision = None
    for attempt in range(2):
        try:
            clean = extract_json(response)
            decision = json.loads(clean)
            break
        except Exception as e:
            print(f"JSON parse attempt {attempt+1} failed. Raw: {response[:500]}")
            if attempt == 0:
                # Retry with stricter prompt
                retry_prompt = "Output ONLY the JSON object. No markdown, no explanation.\n" + prompt
                response = call_groq(retry_prompt)
                if not response:
                    return {"action": "HOLD", "reasoning": "Groq API error on retry"}
            else:
                return {"action": "HOLD", "reasoning": "JSON parse error after retry"}

    if decision is None:
        return {"action": "HOLD", "reasoning": "Failed to parse AI response"}

    # ---------- VALIDATE & ENFORCE RULES ----------
    action = decision.get("action")
    if action in ("LONG", "SHORT"):
        entry = float(decision.get("limit_price", 0))
        stop = float(decision.get("stop_loss", 0))
        if entry <= 0 or stop <= 0:
            return {"action": "HOLD", "reasoning": "Invalid entry or stop"}

        risk = abs(entry - stop)
        if risk <= 0:
            return {"action": "HOLD", "reasoning": "Stop on wrong side"}

        # Cap quantity at 1% risk
        raw_qty = float(decision.get("quantity", 0))
        max_qty = 10 / risk if risk > 0 else 0
        if raw_qty > max_qty:
            decision["quantity"] = round(max_qty, 4)
            decision["reasoning"] += f" | Qty capped to {decision['quantity']} for 1% risk"

        # Ensure TPs meet minimum R multiples
        tp_levels = [f"take_profit_{i}" for i in range(1,7)]
        req_r = [0.2, 0.4, 0.8, 1.2, 1.6, 2.5]
        for i, tp_key in enumerate(tp_levels):
            if tp_key in decision and decision[tp_key] != 0:
                desired_r = req_r[i]
                if action == "LONG":
                    min_tp = entry + desired_r * risk
                    if decision[tp_key] < min_tp:
                        decision[tp_key] = round(min_tp, 6)
                        decision["reasoning"] += f" | TP{i+1} corrected to {min_tp}"
                else:
                    min_tp = entry - desired_r * risk
                    if decision[tp_key] > min_tp:
                        decision[tp_key] = round(min_tp, 6)
                        decision["reasoning"] += f" | TP{i+1} corrected to {min_tp}"

        # Ensure confidence score is at least 6
        conf = int(decision.get("confidence_score", 0))
        if conf < 6:
            return {"action": "HOLD", "reasoning": f"Confidence score {conf} below minimum threshold of 6"}

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
        action = dec.get('action', 'HOLD')
        if action in ["LONG", "SHORT"]:
            msg = (f"📊 {action} {dec.get('symbol')}\n"
                   f"Order: {dec.get('order_type','MARKET')} @ {dec.get('limit_price','CMP')}\n"
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
