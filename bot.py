import requests, json, os, time
from datetime import datetime

TELEGRAM_TOKEN = os.environ["8631237233:AAEc_QP5HnI6qDrfQVaJxRuLG8q6s-C3YEc"]
CHAT_ID = os.environ["0"]
GEMINI_API_KEY = os.environ["AQ.Ab8RN6J48afeUOtT0PNu1p5nnkI9iU8Sm2bMYXU6smnWfy4GVg"]

import google.generativeai as genai
genai.configure(api_key=GEMINI_API_KEY)

BASE = "https://fapi.binance.com"

portfolio = {
    "balance_usdt": 1000.0,
    "positions": [],
    "realized_pnl": 0.0,
    "daily_loss_limit": -50
}

SYSTEM_PROMPT = """
You are "Crypto Institutional Desk – Multi‑Analysis", an autonomous paper trader on USDT perpetuals.
Use ONLY real data fetched via function calls. Never guess numbers.

FUNCTIONS:
- get_order_book(symbol) -> {bid, ask, spread_pct, orderbook_imbalance, last_price, vwap_1h, atr_14, volume_24h}
- get_funding_rate(symbol) -> {funding_rate}
- get_long_short_ratio(symbol) -> {long_short_ratio}
- get_open_interest(symbol) -> {open_interest}

ANALYSIS & SCORING (0-10):
1. Momentum & Microstructure (0-2): last > vwap & imbalance.
2. Positioning (0-2): long/short ratio + OI change.
3. Funding (0-2): near zero = 2.
4. Volatility (0-2): ATR% between 1-8% = 2.
5. Volume (0-2): 24h vol > 500k & rel vol >1.2.

Only trade if score >= 7.

ENTRY: Limit order at bid (long) / ask (short).
SIZE: risk = balance * 0.005 / (atr*1.8), capped to 15% of balance.
EXIT: Stop = entry ± (atr*1.8). TP = entry ± 2*stop_distance minimum. If score >=8 & strong imbalance, TP can be 3:1. NEVER BELOW 2:1.

OUTPUT ONLY a JSON:
{"action":"LONG"|"SHORT"|"HOLD","symbol":"BTCUSDT","quantity":0.01,"order_type":"LIMIT","limit_price":70000,"stop_loss":69800,"take_profit":70400,"confidence_score":8,"reasoning":"..."}
"""

def fetch(endpoint):
    try:
        r = requests.get(BASE + endpoint, timeout=15)
        return r.json()
    except:
        return {"error": "request failed"}

def get_order_book(symbol):
    depth = fetch(f"/fapi/v1/depth?symbol={symbol}&limit=5")
    if "error" in depth: return depth
    bids, asks = depth["bids"], depth["asks"]
    bid, ask = float(bids[0][0]), float(asks[0][0])
    spread_pct = (ask - bid) / ask * 100
    bid_vol = sum(float(b[1]) for b in bids[:3])
    ask_vol = sum(float(a[1]) for a in asks[:3])
    total = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol)/total if total else 0

    klines = fetch(f"/fapi/v1/klines?symbol={symbol}&interval=1m&limit=60")
    if "error" not in klines and len(klines)>=60:
        cv, cvp = 0,0
        trs = []
        for i in range(len(klines)-14, len(klines)):
            h, l, c = float(klines[i][2]), float(klines[i][3]), float(klines[i][4])
            v = float(klines[i][5])
            tp = (h+l+c)/3
            cvp += tp*v; cv += v
            prev_c = float(klines[i-1][4])
            tr = max(h-l, abs(h-prev_c), abs(l-prev_c))
            trs.append(tr)
        vwap = cvp/cv if cv else bid
        atr = sum(trs)/len(trs)
    else:
        vwap, atr = bid, ask-bid

    ticker = fetch(f"/fapi/v1/ticker/24hr?symbol={symbol}")
    last = float(ticker.get("lastPrice", bid)) if "error" not in ticker else bid
    vol = float(ticker.get("quoteVolume", 0)) if "error" not in ticker else 0

    return {"bid":bid, "ask":ask, "spread_pct":spread_pct, "orderbook_imbalance":imbalance,
            "last_price":last, "vwap_1h":vwap, "atr_14":atr, "volume_24h":vol}

def get_funding_rate(symbol):
    d = fetch(f"/fapi/v1/premiumIndex?symbol={symbol}")
    if "error" in d: return d
    return {"funding_rate": float(d["lastFundingRate"])*100}

def get_long_short_ratio(symbol):
    d = fetch(f"/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m")
    if "error" in d or not d: return {"error":"unavailable"}
    return {"long_short_ratio": float(d[0]["longShortRatio"])}

def get_open_interest(symbol):
    d = fetch(f"/fapi/v1/openInterest?symbol={symbol}")
    if "error" in d: return d
    return {"open_interest": float(d["openInterest"]), "oi_change_1h_pct": 0.0}

def call_func(name, args):
    sym = args["symbol"]
    if name == "get_order_book": return get_order_book(sym)
    if name == "get_funding_rate": return get_funding_rate(sym)
    if name == "get_long_short_ratio": return get_long_short_ratio(sym)
    if name == "get_open_interest": return get_open_interest(sym)
    return {"error":"unknown"}

def ai_decision():
    tickers = fetch("/fapi/v1/ticker/24hr")
    if "error" in tickers: return {"action":"HOLD","reasoning":"Binance data unavailable"}
    top = sorted(tickers, key=lambda x: float(x.get("quoteVolume",0)), reverse=True)[:20]
    top_list = [{"symbol":t["symbol"], "volume":float(t["quoteVolume"]), "change":float(t["priceChangePercent"])} for t in top]

    msgs = [{"role":"user", "parts":[{"text":SYSTEM_PROMPT}]},
            {"role":"user", "parts":[{"text":f"Portfolio: {json.dumps(portfolio)}\nTop coins: {json.dumps(top_list)}\nGive ONE trade decision."}]}]

    tools = [
        {"name":"get_order_book","description":"Live order book & ATR","parameters":{"type":"object","properties":{"symbol":{"type":"string"}},"required":["symbol"]}},
        {"name":"get_funding_rate","description":"Funding rate","parameters":{"type":"object","properties":{"symbol":{"type":"string"}},"required":["symbol"]}},
        {"name":"get_long_short_ratio","description":"Long/short ratio","parameters":{"type":"object","properties":{"symbol":{"type":"string"}},"required":["symbol"]}},
        {"name":"get_open_interest","description":"Open interest","parameters":{"type":"object","properties":{"symbol":{"type":"string"}},"required":["symbol"]}}
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
    return {"action":"HOLD","reasoning":"No decision after function calls"}

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    requests.post(url, data=payload)

def main():
    decision = ai_decision()
    msg = f"📊 {decision.get('action','HOLD')} {decision.get('symbol','')}\n" \
          f"Qty: {decision.get('quantity','')} | Score: {decision.get('confidence_score','')}\n" \
          f"Stop: {decision.get('stop_loss','')} TP: {decision.get('take_profit','')}\n" \
          f"Reason: {decision.get('reasoning','')}"
    print(msg)
    send_telegram_message(msg)

if __name__ == "__main__":
    main()
