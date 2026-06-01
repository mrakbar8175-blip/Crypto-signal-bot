import yfinance as yf
import pandas as pd
import numpy as np
import requests, json, os, traceback, time, re
from datetime import datetime, timedelta

# ========== ENVIRONMENT ==========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not set in secrets.")

# ========== PAPER PORTFOLIO ==========
portfolio = {
    "balance_usdt": 1000.0,   # using USD as base
    "positions": [],
    "realized_pnl": 0.0,
    "daily_loss_limit": -20
}

# ========== FOREX PAIRS (Yahoo Finance tickers) ==========
FX_PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "NZDUSD": "NZDUSD=X",
    "USDCAD": "USDCAD=X",
    "USDCHF": "USDCHF=X",
    "EURGBP": "EURGBP=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
}

# ========== YFINANCE HELPERS ==========
def get_4h_data(symbol, days=30):
    """Fetch 4‑hour candles for the last `days`."""
    ticker = FX_PAIRS[symbol]
    end = datetime.now()
    start = end - timedelta(days=days)
    data = yf.download(ticker, start=start, end=end, interval="90m")  # 90min as proxy for 4h? No, we can use 1h and resample.
    # Better: download 1h and resample to 4h
    data = yf.download(ticker, start=start, end=end, interval="1h")
    if data.empty:
        return None
    # Resample to 4h
    data_4h = data.resample('4H').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    return data_4h

def get_current_price(symbol):
    """Last close price as current price."""
    ticker = FX_PAIRS[symbol]
    data = yf.download(ticker, period="1d", interval="5m")
    if not data.empty:
        return float(data['Close'].iloc[-1])
    return None

# ---------- TECHNICAL INDICATORS ----------
def compute_atr(df, period=14):
    high = df['High']
    low = df['Low']
    close = df['Close']
    tr = pd.concat([high - low, 
                    (high - close.shift()).abs(), 
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return atr

def compute_sma(df, period=50):
    return df['Close'].rolling(period).mean().iloc[-1]

def compute_rsi(df, period=14):
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

# ---------- SCORING ----------
def score_pair(symbol):
    """Return conviction score -3..+3 based on trend, momentum, volatility, carry, DXY."""
    df = get_4h_data(symbol, days=30)
    if df is None or len(df) < 20:
        return 0, None

    current_price = df['Close'].iloc[-1]
    atr = compute_atr(df)
    sma50 = compute_sma(df)
    rsi = compute_rsi(df)

    # 1. Trend (25%): Price vs SMA50
    trend_signal = 1 if current_price > sma50 else -1
    score = 0.25 * trend_signal * 3

    # 2. Momentum (15%): RSI extremes
    if rsi < 30:
        momentum_signal = 1   # oversold bounce potential
    elif rsi > 70:
        momentum_signal = -1  # overbought reversal potential
    else:
        momentum_signal = 0
    score += 0.15 * momentum_signal * 3

    # 3. Volatility (10%): ATR as % of price – prefer moderate volatility
    vol_pct = (atr / current_price) * 100
    if 0.5 < vol_pct < 2.0:
        vol_signal = 1   # tradeable
    elif vol_pct > 3.0:
        vol_signal = -1  # too wild
    else:
        vol_signal = 0
    score += 0.10 * vol_signal * 3

    # 4. Carry / Interest rate differential (10%) – simplified: use DXY direction
    # If USD is strong (DXY up), USD pairs benefit. We'll approximate with DXY trend.
    dxy_data = yf.download("DX-Y.NYB", period="5d", interval="1h")
    if not dxy_data.empty:
        dxy_change = (dxy_data['Close'].iloc[-1] / dxy_data['Close'].iloc[-48]) - 1 if len(dxy_data) > 48 else 0
        # For pairs like EURUSD, DXY up is bearish. We'll adjust per symbol.
        if symbol in ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]:
            carry_signal = -1 if dxy_change > 0.002 else (1 if dxy_change < -0.002 else 0)
        elif symbol in ["USDJPY", "USDCAD", "USDCHF"]:
            carry_signal = 1 if dxy_change > 0.002 else (-1 if dxy_change < -0.002 else 0)
        else:  # crosses like EURGBP etc.
            carry_signal = 0
        score += 0.10 * carry_signal * 3

    # 5. Macro / Sentiment (20%): we'll use a simple fear/greed proxy from VIX
    vix_data = yf.download("^VIX", period="5d", interval="1h")
    if not vix_data.empty:
        vix = vix_data['Close'].iloc[-1]
        if vix < 15:
            macro_signal = 1   # calm market, risk-on
        elif vix > 25:
            macro_signal = -1  # fear, risk-off
        else:
            macro_signal = 0
        score += 0.20 * macro_signal * 3

    # 6. Economic calendar / news (5%) – we'll skip for now; set neutral
    score += 0.05 * 0 * 3

    return max(-3, min(3, score)), {"atr": atr, "current_price": current_price, "sma50": sma50, "rsi": rsi}

# ---------- AI REASONING ----------
def call_groq_reasoning(symbol, entry, atr, direction):
    prompt = (
        f"Forex trade signal: {direction} {symbol} @ {entry:.5f}. 4h ATR: {atr:.5f}. "
        "Provide a short reasoning (1 sentence) and confidence 1-10.\n"
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
            reason = reason_match.group(1).strip() if reason_match else "Automated forex signal."
            return conf, reason
    except:
        pass
    return 6, "Multi-factor forex model signal."

# ========== MAIN SIGNAL GENERATION ==========
def generate_signal():
    best_pair = None
    best_score = 0
    best_details = None

    for symbol in FX_PAIRS.keys():
        score, details = score_pair(symbol)
        if details is None:
            continue
        if best_pair is None or abs(score) > abs(best_score):
            best_pair = symbol
            best_score = score
            best_details = details

    if best_pair is None or abs(best_score) < 1.5:
        return {"action": "HOLD", "reasoning": f"No strong conviction. Best score: {best_score:.2f} for {best_pair or 'none'}."}

    direction = "LONG" if best_score > 0 else "SHORT"
    entry = best_details["current_price"]
    atr = best_details["atr"]
    min_stop = max(1.5 * atr, entry * 0.002)   # 0.2% min stop for forex
    stop = entry - min_stop if direction == "LONG" else entry + min_stop
    stop = round(stop, 5)
    risk = abs(entry - stop)
    qty = round(10 / risk, 2)   # 1% of 1000 USD = 10 USD risk

    tps = []
    for mult in [0.2, 0.4, 0.8, 1.2, 1.6, 2.5]:
        if direction == "LONG":
            tps.append(round(entry + mult * risk, 5))
        else:
            tps.append(round(entry - mult * risk, 5))

    conf, reason = call_groq_reasoning(best_pair, entry, atr, direction)
    if conf < 6:
        return {"action": "HOLD", "reasoning": f"AI confidence too low ({conf}/10). {reason}"}

    return {
        "action": direction,
        "symbol": best_pair,
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
            msg = (f"📊 FOREX {action} {dec.get('symbol')}\n"
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
