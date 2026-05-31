import requests, json, os, time
from pycoingecko import CoinGeckoAPI
import google.generativeai as genai

# Read secrets from environment
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

genai.configure(api_key=GEMINI_API_KEY)
cg = CoinGeckoAPI()

# Paper portfolio
portfolio = {
    "balance_usdt": 1000.0,
    "positions": [],
    "realized_pnl": 0.0,
    "daily_loss_limit": -50
}

# Stable list of CoinGecko IDs for top USDT perpetuals (mapped to Binance symbols)
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

SYSTEM_PROMPT = """
You are "Crypto Institutional Desk – Multi‑Analysis", a paper trader on USDT perpetuals.
Use ONLY the provided function calls. Never guess numbers.

FUNCTIONS:
- get_order_book(symbol) -> {bid, ask, spread_pct, orderbook_imbalance, last_price, vwap_1h, atr_14, volume_24h}
- get_funding_rate(symbol) -> {funding_rate}
- get_long_short_ratio(symbol) -> {long_short_ratio}
- get_open_interest(symbol) -> {open_interest}

SCORING (0-10):
1. Momentum & Micro (0-2): last > vwap & imbalance.
2. Positioning (0-2): long/short ratio + OI change.
3. Funding (0-2): near zero = 2.
4. Volatility (0-2): ATR% 1-8% = 2.
5. Volume (0-2): 24h vol > 500k USDT & rel vol > 1.2.

Only trade if score >= 7.

ENTRY: Limit at bid (long) / ask (short).
SIZE: risk = balance*0.005 / (ATR*1.8), cap 15% of balance.
EXIT: Stop = entry ± (ATR*1.8). TP min = entry ± 2*stop. Can extend to 3:1 if score>=8 & strong imbalance. NEVER below 2:1.

OUTPUT ONLY JSON:
{"action":"LONG"|"SHORT"|"HOLD","symbol":"BTCUSDT","quantity":0.01,"order_type":"LIMIT","limit_price":70000,"stop_loss":69800,"take_profit":70400,"confidence_score":8,"reasoning":"..."}
"""

def fetch_binance(endpoint):
    """Try Binance; if blocked, return error dict."""
    try:
        r = requests.get("https://fapi.binance.com" + endpoint, timeout=10)
        return r.json()
    except:
        return {"error": "request failed"}

def get_cg_price(coin_id):
    """Get price in USDT from CoinGecko."""
    try:
        data = cg.get_price(ids=coin_id, vs_currencies='usd')
        return data[coin_id]['usd']
    except:
        return 0

def get_order_book(symbol):
    # Try Binance first, fallback to CoinGecko approximation
    depth = fetch_binance(f"/fapi/v1/depth?symbol={symbol}&limit=5")
    if "error" not in depth and "bids" in depth:
        bids, asks = depth["bids"], depth["asks"]
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        spread_pct = (ask - bid) / ask * 100
        bid_vol = sum(float(b[1]) for b in bids[:3])
        ask_vol = sum(float(a[1]) for a in asks[:3])
        total = bid_vol + ask_vol
        imbalance = (bid_vol - ask_vol) / total if total else 0
    else:
        # Fallback: use CoinGecko price as mid
        coin_id = [k for k,v in COIN_MAP.items() if v == symbol][0]
        price = get_cg_price(coin_id)
        if price == 0: return {"error": "price unavailable"}
        bid = price * 0.9999
        ask = price * 1.0001
        spread_pct = 0.02
        imbalance = 0
    # ATR and VWAP from Binance klines, or approximate
    klines = fetch_binance(f"/fapi/v1/klines?symbol={symbol}&interval=1m&limit=60")
    if "error" not in klines and len(klines) >= 60:
        cv, cvp = 0,0
        trs = []
        for i in range(len(klines)-14, len(klines)):
            h,l,c = float(klines[i][2]), float(klines[i][3]), float(klines[i][4])
            v = float(klines[i][5])
            tp = (h+l+c)/3
            cvp += tp*v; cv += v
            prev_c = float(klines[i-1][4])
            tr = max(h-l, abs(h-prev_c), abs(l-prev_c))
            trs.append(tr)
        vwap = cvp/cv if cv else bid
        atr = sum(trs)/len(trs)
    else:
        # Approximate ATR as 1.5% of price
        vwap = bid
        atr = bid * 0.015
    volume = 0
    ticker = fetch_binance(f"/fapi/v1/ticker/24hr?symbol={symbol}")
    if "error" not in ticker:
        volume = float(ticker.get("quoteVolume", 0))
    else:
        volume = 1000000  # assume liquid
    return {"bid":bid, "ask":ask, "spread_pct":spread_pct, "orderbook_imbalance":imbalance,
            "last_price":bid, "vwap_1h":vwap, "atr_14":atr, "volume_24h":volume}

def get_funding_rate(symbol):
    d = fetch_binance(f"/fapi/v1/premiumIndex?symbol={symbol}")
    if "error" in d: return {"funding_rate": 0.01}  # assume neutral
    return {"funding_rate": float(d["lastFundingRate"])*100}

def get_long_short_ratio(symbol):
    d = fetch_binance(f"/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m")
    if "error" in d or not d: return {"error":"unavailable"}
    return {"long_short_ratio": float(d[0]["longShortRatio"])}

def get_open_interest(symbol):
    d = fetch_binance(f"/fapi/v1/openInterest?symbol={symbol}")
    if "error" in d: return {"open_interest": 0, "oi_change_1h_pct": 0.0}
    return {"open_interest": float(d["openInterest"]), "oi_change_1h_pct": 0.0}

def call_func(name, args):
    sym = args["symbol"]
    if name == "get_order_book": return get_order_book(sym)
    if name == "get_funding_rate": return get_funding_rate(sym)
    if name == "get_long_short_ratio": return get_long_short_ratio(sym)
    if name == "get_open_interest": return get_open_interest(sym)
    return {"error":"unknown"}

def ai_decision():
    # Get top coins by volume from CoinGecko
    try:
        top_coins = cg.get_coins_markets(vs_currency='usd', order='volume_desc', per_page=15, page=1)
        top_list = []
        for coin in top_coins:
            symbol = COIN_MAP.get(coin['id'])
            if symbol:
                top_list.append({
                    "symbol": symbol,
                    "volume": coin.get('total_volume', 0),
                    "change": coin.get('price_change_percentage_24h', 0)
                })
        if not top_list:
            raise ValueError("No matching coins")
    except Exception as e:
        print("CoinGecko failed, using fallback list:", e)
        # Fallback static list
        top_list = [{"symbol": s, "volume": 0, "change": 0} for s in COIN_MAP.values()]

    print("Top coins:", len(top_list))

    msgs = [{"role":"user", "parts":[{"text":SYSTEM_PROMPT}]},
            {"role":"user", "parts":[{"text":f"Portfolio: {json.dumps(portfolio)}\nTop coins: {json.dumps(top_list)}\nGive ONE trade decision."}]}]

    # Fixed tool definitions (no "type": "object")
    tools = [
        {
            "name": "get_order_book",
            "description": "Live order book & ATR",
            "parameters": {
                "properties": {
                    "symbol": {"type": "string"}
                },
                "required": ["symbol"]
            }
        },
        {
            "name": "get_funding_rate",
            "description": "Funding rate",
            "parameters": {
                "properties": {
                    "symbol": {"type": "string"}
                },
                "required": ["symbol"]
            }
        },
        {
            "name": "get_long_short_ratio",
            "description": "Long/short ratio",
            "parameters": {
                "properties": {
                    "symbol": {"type": "string"}
                },
                "required": ["symbol"]
            }
        },
        {
            "name": "get_open_interest",
            "description": "Open interest",
            "parameters": {
                "properties": {
                    "symbol": {"type": "string"}
                },
                "required": ["symbol"]
            }
        }
    ]

    model = genai.GenerativeModel("gemini-1.5-flash", tools=tools)
    for _ in range(10):
        resp = model.generate_content(msgs)
        part = resp.candidates[0].content.parts[0]
        if "function_call" in part:
            fn = part["function_call"]["name"]
            args = part["function_call"]["args"]
            result = call_func(fn, args)
            msgs.append({"role":"user","parts":[{"function_response":{"name":fn,"response":result}}]})
        else:
            raw = part.text
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0]
            try:
                return json.loads(raw)
            except:
                return {"action":"HOLD","reasoning":"JSON error"}
    return {"action":"HOLD","reasoning":"No decision"}

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
        print("Telegram response:", resp.status_code, resp.text)
    except Exception as e:
        print("Telegram send failed:", e)

def main():
    dec = ai_decision()
    msg = f"📊 {dec.get('action','HOLD')} {dec.get('symbol','')}\n" \
          f"Qty: {dec.get('quantity','')} | Score: {dec.get('confidence_score','')}\n" \
          f"Stop: {dec.get('stop_loss','')} TP: {dec.get('take_profit','')}\n" \
          f"Reason: {dec.get('reasoning','')}"
    print(msg)
    send_telegram(msg)

if __name__ == "__main__":
    main()
