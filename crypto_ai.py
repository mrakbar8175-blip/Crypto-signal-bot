import requests, json, os, traceback, re, time, random
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ========== ENVIRONMENT ==========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not set in secrets.")

# ========== PERSISTENT PORTFOLIO ==========
PORTFOLIO_FILE = "portfolio.json"

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE) as f:
                data = json.load(f)
            return {
                "balance_usdt": data.get("balance_usdt", 1000.0),
                "realized_pnl": data.get("realized_pnl", 0.0),
                "open_positions": data.get("open_positions", 0),
                "daily_loss_limit": data.get("daily_loss_limit", -20)
            }
        except:
            pass
    return {
        "balance_usdt": 1000.0,
        "realized_pnl": 0.0,
        "open_positions": 0,
        "daily_loss_limit": -20
    }

def save_portfolio(p):
    try:
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(p, f, indent=2)
    except:
        print("Warning: Could not save portfolio.json")

portfolio = load_portfolio()

# ========== CSV FILES ==========
TRADE_LOG_CSV = "trade_log.csv"
OPEN_TRADES_CSV = "open_trades.csv"
TRADE_RESULTS_CSV = "trade_results.csv"

# ========== COINGECKO DATA FETCH (sole source for 4h candles) ==========
# Mapping from CoinGecko ticker -> coin ID
COINGECKO_ID_MAP = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "BNBUSDT": "binancecoin",
    "XRPUSDT": "ripple",
    "ADAUSDT": "cardano",
    "SOLUSDT": "solana",
    "DOGEUSDT": "dogecoin",
    "AVAXUSDT": "avalanche-2",
    "DOTUSDT": "polkadot",
    "LINKUSDT": "chainlink",
    "LTCUSDT": "litecoin",
    "NEARUSDT": "near",
    "ATOMUSDT": "cosmos",
    "ETCUSDT": "ethereum-classic",
    "FILUSDT": "filecoin",
    "ARBUSDT": "arbitrum",
    "OPUSDT": "optimism",
    "INJUSDT": "injective-protocol",
    "TIAUSDT": "celestia",
    "SEIUSDT": "sei-network",
    "RUNEUSDT": "thorchain",
    "GRTUSDT": "the-graph",
    "AAVEUSDT": "aave",
    "ALGOUSDT": "algorand",
    "SANDUSDT": "the-sandbox",
    "MANAUSDT": "decentraland",
    "THETAUSDT": "theta-token",
    "FTMUSDT": "fantom",
    "EOSUSDT": "eos",
    "MKRUSDT": "maker",
    "LDOUSDT": "lido-dao",
    "IMXUSDT": "immutable-x",
    "FLOWUSDT": "flow",
    "XTZUSDT": "tezos",
    "NEOUSDT": "neo",
    "KSMUSDT": "kusama",
    "ZECUSDT": "zcash",
    "DASHUSDT": "dash",
    "EGLDUSDT": "elrond-erd-2",
    "MINAUSDT": "mina-protocol",
    "GALAUSDT": "gala",
    "HNTUSDT": "helium",
    "CFXUSDT": "conflux-token",
    "ARUSDT": "arweave",
    "FETUSDT": "fetch-ai",
    "AGIXUSDT": "singularitynet",
    "OCEANUSDT": "ocean-protocol",
    "1INCHUSDT": "1inch",
    "CRVUSDT": "curve-dao-token",
    "AXSUSDT": "axie-infinity",
    "CHZUSDT": "chiliz",
    "ENJUSDT": "enjincoin",
    "BATUSDT": "basic-attention-token",
    "SNXUSDT": "synthetix-network-token",
    "COMPUSDT": "compound-governance-token",
    "YFIUSDT": "yearn-finance",
    "SUSHIUSDT": "sushi",
    "ZRXUSDT": "0x",
    "RENUSDT": "republic-protocol",
    "CELOUSDT": "celo",
    "LRCUSDT": "loopring",
    "ANKRUSDT": "ankr",
    "STORJUSDT": "storj",
    "COTIUSDT": "coti",
    "KAVAUSDT": "kava",
    "ICXUSDT": "icon",
    "ONTUSDT": "ontology",
    "ZILUSDT": "zilliqa",
    "WAVESUSDT": "waves",
    "QTUMUSDT": "qtum",
    "OMGUSDT": "omisego",
    "BANDUSDT": "band-protocol",
    "DENTUSDT": "dent",
    "HOTUSDT": "holotoken",
    "IOSTUSDT": "iostoken",
    "RVNUSDT": "ravencoin",
    "SCUSDT": "siacoin",
    "ZENUSDT": "horizen",
    "CKBUSDT": "nervos-network",
    "SKLUSDT": "skale",
    "CTSIUSDT": "cartesi",
    "CTKUSDT": "certik",
    "LINAUSDT": "linear",
    "TRBUSDT": "tellor",
    "BALUSDT": "balancer",
    "PERPUSDT": "perpetual-protocol",
    "BNTUSDT": "bancor",
    "RSRUSDT": "reserve-rights-token",
    "TOMOUSDT": "tomochain",
    "DGBUSDT": "digibyte",
    "DUSKUSDT": "dusk-network",
    "REEFUSDT": "reef",
    "ALPHAUSDT": "alpha-finance",
    "FORTHUSDT": "ampleforth-governance-token",
    "POLSUSDT": "polkastarter",
    "C98USDT": "coin98",
    "RAREUSDT": "superrare",
    "ATAUSDT": "automata",
    "IDEXUSDT": "idex",
    "MLNUSDT": "melon",
    "PEPEUSDT": "pepe",
    "WIFUSDT": "dogwifcoin",
    "BONKUSDT": "bonk",
    "FLOKIUSDT": "floki",
    "APTUSDT": "aptos",
    "SUIUSDT": "sui",
    "WLDUSDT": "worldcoin-wld",
    "XLMUSDT": "stellar",
    "TRXUSDT": "tron",
    "BCHUSDT": "bitcoin-cash",
    "WBTUSDT": "whitebit",
    "USDCUSDT": "usd-coin",
    "DAIUSDT": "dai",
    "PYUSDUSDT": "paypal-usd",
    "XAUTUSDT": "tether-gold",
    "PAXGUSDT": "pax-gold",
    "JTOUSDT": "jito-governance-token",
    "ONDOUSDT": "ondo-finance",
    "IDUSDT": "space-id",
    "XMRUSDT": "monero",
    "ZECUSDT": "zcash",
    "INJUSDT": "injective-protocol",
    "QUQUSDT": "quq",
    "MPRAUSDT": "mpra",
    "SPYXUSDT": "spyx",
    "NIGHTUSDT": "night",
    "RLUSDUSDT": "rlusd",
    "SHEBUSDT": "sheb",
    "HYPEUSDT": "hype",
    "USD1USDT": "usd1",
    "UNIUSDT": "uniswap",
    "ASTERUSDT": "aster",
    "FDUSDUSDT": "first-digital-usd",
    "HBARUSDT": "hedera-hashgraph",
}

def get_cg_ohlc(symbol_usdt, days=14):
    """Fetch 4h OHLC candles from CoinGecko. Returns DataFrame with Open, High, Low, Close, Volume."""
    coin_id = COINGECKO_ID_MAP.get(symbol_usdt)
    if not coin_id:
        # Fallback: construct ID from lowercase ticker (may not always work)
        coin_id = symbol_usdt.replace("USDT", "").lower()
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc?vs_currency=usd&days={days}"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return pd.DataFrame()
        data = resp.json()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data, columns=["time", "open", "high", "low", "close"])
        df["time"] = pd.to_datetime(df["time"], unit='ms')
        df.set_index("time", inplace=True)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col])
        # CoinGecko OHLC does not include volume – we'll estimate from price range later
        df["Volume"] = (df["high"] - df["low"]) * 1000   # rough placeholder
        df.columns = ["Open", "High", "Low", "Close", "Volume"]
        return df
    except:
        return pd.DataFrame()

# Alias for consistency
get_4h_klines = get_cg_ohlc

# ========== COINGECKO MACRO BIAS ==========
def fetch_coingecko(url, retries=2):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(2 ** attempt)
        except:
            time.sleep(1)
    return None

def get_macro_bias():
    bias = 0.0
    try:
        cg_simple = fetch_coingecko(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
        )
        if cg_simple and 'bitcoin' in cg_simple:
            change = cg_simple['bitcoin'].get('usd_24h_change', 0)
            bias += max(-1.0, min(1.0, change / 5.0)) * 0.6
    except:
        pass
    try:
        cg_daily = fetch_coingecko(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=14"
        )
        if cg_daily and 'prices' in cg_daily:
            prices = cg_daily['prices']
            if len(prices) >= 7:
                daily = [p[1] for p in prices[-7:]]
                ema = daily[0]
                for p in daily[1:]:
                    ema = p * 0.25 + ema * 0.75
                current = daily[-1]
                if current > 0:
                    deviation = (current - ema) / current
                    bias += max(-1.0, min(1.0, deviation * 20)) * 0.4
    except:
        pass
    return max(-1.0, min(1.0, bias))

# ========== CSV LOGGING ==========
def init_csv(filepath, columns):
    if not os.path.exists(filepath):
        df = pd.DataFrame(columns=columns)
        df.to_csv(filepath, index=False)

def append_csv(filepath, df_new):
    try:
        existing = pd.read_csv(filepath)
        updated = pd.concat([existing, df_new], ignore_index=True)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        updated = df_new
    updated.to_csv(filepath, index=False)

def save_csv(filepath, df):
    df.to_csv(filepath, index=False)

def initialize_trade_files():
    init_csv(TRADE_LOG_CSV, ["timestamp", "symbol", "action", "entry", "stop",
                             "TP1", "TP2", "TP3", "TP4", "TP5", "conviction", "ai_confidence"])
    init_csv(OPEN_TRADES_CSV, ["timestamp", "symbol", "action", "entry", "stop",
                               "TP1", "TP2", "TP3", "TP4", "TP5", "status",
                               "quantity", "original_qty", "highest_tp"])
    init_csv(TRADE_RESULTS_CSV, ["timestamp", "symbol", "action", "entry", "stop",
                                 "TP1", "TP2", "TP3", "TP4", "TP5", "status", "hit_level",
                                 "close_time", "exit_price", "quantity", "pnl_usdt"])

def log_signal(signal):
    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": signal["symbol"],
        "action": signal["action"],
        "entry": signal["limit_price"],
        "stop": signal["stop_loss"],
        "TP1": signal["take_profits"][0] if len(signal["take_profits"]) > 0 else "",
        "TP2": signal["take_profits"][1] if len(signal["take_profits"]) > 1 else "",
        "TP3": signal["take_profits"][2] if len(signal["take_profits"]) > 2 else "",
        "TP4": signal["take_profits"][3] if len(signal["take_profits"]) > 3 else "",
        "TP5": signal["take_profits"][4] if len(signal["take_profits"]) > 4 else "",
        "conviction": signal["conviction_score"],
        "ai_confidence": signal["confidence_score"],
    }
    df = pd.DataFrame([row])
    append_csv(TRADE_LOG_CSV, df)

def add_open_trade(signal):
    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": signal["symbol"],
        "action": signal["action"],
        "entry": signal["limit_price"],
        "stop": signal["stop_loss"],
        "TP1": signal["take_profits"][0],
        "TP2": signal["take_profits"][1],
        "TP3": signal["take_profits"][2],
        "TP4": signal["take_profits"][3],
        "TP5": signal["take_profits"][4],
        "status": "open",
        "quantity": signal["quantity"],
        "original_qty": signal["quantity"],
        "highest_tp": -1
    }
    df = pd.DataFrame([row])
    append_csv(OPEN_TRADES_CSV, df)

# ========== PORTFOLIO HELPERS ==========
def get_daily_pnl():
    try:
        df = pd.read_csv(TRADE_RESULTS_CSV)
        if df.empty:
            return 0.0
        today = datetime.now().strftime("%Y-%m-%d")
        df['close_time'] = pd.to_datetime(df['close_time'])
        daily = df[df['close_time'].dt.strftime("%Y-%m-%d") == today]
        if daily.empty:
            return 0.0
        return daily['pnl_usdt'].sum()
    except:
        return 0.0

def update_portfolio(trade_result):
    portfolio['balance_usdt'] += trade_result['pnl_usdt']
    portfolio['realized_pnl'] += trade_result['pnl_usdt']
    save_portfolio(portfolio)

# ========== TP5‑FOCUSED LAYERS ==========
def safe_ema(series, span):
    try:
        return series.ewm(span=span, adjust=False).mean()
    except:
        return pd.Series(index=series.index)

def get_atr(df, current_price):
    try:
        if df.empty or len(df) < 14:
            return current_price * 0.02
        high, low, close = df['High'], df['Low'], df['Close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = safe_ema(tr, 14).iloc[-1]
        return atr if not pd.isna(atr) else current_price * 0.02
    except:
        return current_price * 0.02

def trend_strength_score(df, direction):
    try:
        n = len(df)
        if n < 20:
            return 0
        closes = df['Close']
        highs = df['High']
        lows = df['Low']
        ema50 = safe_ema(closes, 50) if n >= 50 else safe_ema(closes, n)
        current = closes.iloc[-1]
        ema_now = ema50.iloc[-1]
        if len(ema50) >= 6:
            slope_up = ema_now > ema50.iloc[-6]
        else:
            slope_up = True
        price_above = current > ema_now

        period = 14
        dm_plus = highs.diff()
        dm_minus = -lows.diff()
        dm_plus[dm_plus < 0] = 0
        dm_minus[dm_minus < 0] = 0
        tr = pd.concat([highs - lows, (highs - closes.shift()).abs(), (lows - closes.shift()).abs()], axis=1).max(axis=1)
        atr = safe_ema(tr, period)
        di_plus = 100 * (safe_ema(dm_plus, period) / atr)
        di_minus = 100 * (safe_ema(dm_minus, period) / atr)
        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
        adx = safe_ema(dx, period).iloc[-1]
        if pd.isna(adx):
            adx = 0

        base_score = 0
        if direction == "LONG":
            if price_above: base_score += 0.4
            if slope_up: base_score += 0.3
            if adx > 25 and di_plus.iloc[-1] > di_minus.iloc[-1]:
                base_score += 0.3
            return base_score
        else:
            if not price_above: base_score += 0.4
            if not slope_up: base_score += 0.3
            if adx > 25 and di_minus.iloc[-1] > di_plus.iloc[-1]:
                base_score += 0.3
            return -base_score
    except:
        return 0

def sustained_momentum_score(df, direction):
    try:
        if len(df) < 13:
            return 0
        closes = df['Close']
        roc6 = (closes.iloc[-1] / closes.iloc[-7] - 1) * 100
        roc12 = (closes.iloc[-1] / closes.iloc[-13] - 1) * 100
        score6 = max(-1.0, min(1.0, roc6 / 2.0))
        score12 = max(-1.0, min(1.0, roc12 / 2.0))
        combined = score6 * 0.6 + score12 * 0.4
        if direction == "LONG":
            return combined
        else:
            return -combined
    except:
        return 0

def volume_expansion_score(df, direction):
    try:
        if len(df) < 48:
            return 0
        short_vol = df['Volume'].iloc[-12:].mean()
        long_vol = df['Volume'].iloc[-48:].mean()
        if long_vol == 0:
            return 0
        ratio = short_vol / long_vol
        expansion = (ratio - 1) * 2
        expansion = max(-1.0, min(1.0, expansion))
        last = df.iloc[-1]
        if direction == "LONG":
            if last['Close'] > last['Open']:
                return expansion
            else:
                return -expansion
        else:
            if last['Close'] < last['Open']:
                return expansion
            else:
                return -expansion
    except:
        return 0

def breakout_candle_score(df, direction, atr):
    try:
        if df.empty:
            return 0
        last = df.iloc[-1]
        body = abs(last['Close'] - last['Open'])
        if body < 1.5 * atr:
            return 0
        candle_range = last['High'] - last['Low']
        if candle_range == 0:
            return 0
        if direction == "LONG":
            if last['Close'] > last['Open'] and (last['Close'] - last['Low']) / candle_range > 0.7:
                return 1.0
            else:
                return -0.5
        else:
            if last['Close'] < last['Open'] and (last['High'] - last['Close']) / candle_range > 0.7:
                return 1.0
            else:
                return -0.5
    except:
        return 0

def relative_strength_score(df, direction, btc_df):
    try:
        if df.empty or len(df) < 13 or btc_df.empty or len(btc_df) < 13:
            return 0
        coin_perf = (df['Close'].iloc[-1] / df['Close'].iloc[-13] - 1)
        btc_perf = (btc_df['Close'].iloc[-1] / btc_df['Close'].iloc[-13] - 1)
        rs = coin_perf - btc_perf
        if direction == "LONG":
            return max(-1.0, min(1.0, rs * 10))
        else:
            return -max(-1.0, min(1.0, rs * 10))
    except:
        return 0

# ========== SCORING ENGINE ==========
def score_coin(symbol, price, volume, macro_bias, coin_data_cache, btc_df):
    try:
        if symbol in coin_data_cache:
            df = coin_data_cache[symbol]
        else:
            df = get_4h_klines(symbol, days=14)
            coin_data_cache[symbol] = df

        if df.empty or len(df) < 48:
            return 0, {"trend":0,"momentum":0,"volume":0,"breakout":0,"rs":0}, "up", price*0.02

        last_candle = df.iloc[-1]
        if last_candle['Close'] > last_candle['Open']:
            direction = "LONG"
        else:
            direction = "SHORT"

        atr_val = get_atr(df, price)

        trend = trend_strength_score(df, direction)
        mom = sustained_momentum_score(df, direction)
        vol = volume_expansion_score(df, direction)
        brk = breakout_candle_score(df, direction, atr_val)
        rs = relative_strength_score(df, direction, btc_df)

        core = (0.30 * trend + 0.25 * mom + 0.20 * vol + 0.15 * brk + 0.10 * rs)

        if core >= 0:
            macro_mult = 1.0 + 0.5 * macro_bias
        else:
            macro_mult = 1.0 - 0.5 * macro_bias

        total = core * macro_mult

        signs = [1 if s > 0.2 else (-1 if s < -0.2 else 0) for s in [trend, mom, vol, brk, rs]]
        aligned = sum(1 for s in signs if (direction == "LONG" and s == 1) or (direction == "SHORT" and s == -1))
        if aligned >= 4:
            total += 0.3 if direction == "LONG" else -0.3

        total = max(-3.0, min(3.0, total))

        layers = {
            "trend": trend,
            "momentum": mom,
            "volume": vol,
            "breakout": brk,
            "rs": rs,
            "macro_bias": macro_bias
        }
        return total, layers, direction, atr_val
    except Exception as e:
        print(f"score_coin error {symbol}: {e}")
        return 0, {"trend":0,"momentum":0,"volume":0,"breakout":0,"rs":0,"macro_bias":0}, "up", price*0.02

def compute_confidence(layers):
    scores = [layers["trend"], layers["momentum"], layers["volume"], layers["breakout"]]
    bear = sum(1 for s in scores if s < -0.3)
    bull = sum(1 for s in scores if s > 0.3)
    aligned = max(bear, bull)
    if aligned >= 4: return 7
    if aligned >= 3: return 6
    if aligned >= 2: return 5
    return 4

# ========== LLAMA QUALITY FILTER (top 5) ==========
def evaluate_deep(coin, direction, macro_bias):
    sym = coin["symbol"]
    price = coin["price"]
    atr = coin["atr"]
    layers = coin["layers"]

    prompt = (
        f"Symbol: {sym} | Direction: {direction} | Price: {price:.4f} | ATR: {atr:.4f}\n"
        f"Macro Bias (BTC): {macro_bias:.2f} (‑1 bearish, +1 bullish).\n"
        f"Layers: trend={layers['trend']:.2f}, momentum={layers['momentum']:.2f}, "
        f"volume={layers['volume']:.2f}, breakout={layers['breakout']:.2f}, rs={layers['rs']:.2f}.\n\n"
        "You are a senior crypto analyst. Rate this setup from 1 to 10, where 10 is a perfect, high‑probability trade "
        "for a 5R move (strong trend, momentum, and volume). "
        "Be strict: only give a high rating if the setup is clean and has a clear edge for a large move. "
        "If the data provided is insufficient to judge, return a rating of 5 and state that the data is limited. "
        "Do not invent patterns or structures that are not explicitly supported by the given data. "
        "Give a very brief reason for your rating. Output format exactly:\n"
        "RATING: 8 | REASON: [short reason]"
    )

    def call_llama(p, temp=0.3):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": p}],
            "temperature": temp,
            "max_tokens": 150
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=40)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except:
            pass
        return None

    text = call_llama(prompt, 0.3)
    rating = 5
    reason = ""
    if text:
        rat_match = re.search(r'RATING:\s*(\d+)', text)
        if rat_match:
            rating = int(rat_match.group(1))
            rating = max(1, min(10, rating))
        reason_match = re.search(r'REASON:\s*(.*)', text)
        if reason_match:
            reason = reason_match.group(1).strip()
    return rating, reason

# ========== VIRAL POST GENERATOR ==========
def generate_post(coin, direction, entry, stop, tps, sl_pct, qwen_reason=""):
    sym = coin["symbol"]; ticker = sym.replace("USDT","")
    price = coin["price"]; atr = coin["atr"]; layers = coin["layers"]

    recent_candles = ""
    try:
        df = get_4h_klines(sym, days=2)
        if not df.empty:
            last_candles = df.tail(6)
            candle_list = []
            for idx, row in last_candles.iterrows():
                candle_list.append(
                    f"O:{row['Open']:.4f} H:{row['High']:.4f} L:{row['Low']:.4f} C:{row['Close']:.4f} V:{row['Volume']:.0f}"
                )
            recent_candles = "\n".join(candle_list)
    except:
        pass

    tp_str = f"{tps[0]:.6f} (0.5R) / {tps[1]:.6f} (1R) / {tps[2]:.6f} (2R) / {tps[3]:.6f} (3R) / {tps[4]:.6f} (5R)"

    system_msg = (
        "You are a legendary crypto chart analyst with 15 years of experience. "
        "Your posts on Binance Square go viral every time because you combine deep technical knowledge with a natural, "
        "human storytelling style. You identify candlestick patterns, chart formations, and key levels like a pro. "
        "Your hooks are irresistible – they create curiosity and FOMO. "
        "Your analysis is detailed yet easy to read, breaking down trend, momentum, volume, and market context. "
        "You always include exact risk‑managed levels and end with an engaging question. "
        "Use emojis sparingly but effectively to highlight key points. "
        "Never output 'RATING:' or any meta commentary. "
        "IMPORTANT: Mention specific candlestick patterns (e.g., bullish engulfing, doji, hammer, shooting star) "
        "and chart structures (e.g., ascending triangle, double bottom, breakout of resistance) only if they are clearly "
        "visible in the candle data provided. If the data is insufficient, focus on the technical indicators."
    )

    user_prompt = (
        f"Write a Binance Square post for a {direction} setup on ${ticker} (USDT pair, 4‑hour chart).\n\n"
        f"Recent 4‑hour candles (newest first):\n{recent_candles}\n\n"
        f"Technical context:\n"
        f"- Price: {price:.4f}\n"
        f"- Momentum: {'bullish' if layers['momentum']>0 else 'bearish'}. Trend: {'up' if layers['trend']>0 else 'down'}.\n"
        f"- Volume expansion: {'yes' if layers['volume']>0 else 'no'}. Breakout signal: {'yes' if layers['breakout']>0 else 'no'}.\n"
        f"- BTC macro bias: {layers['macro_bias']:.2f} (‑1 bearish, +1 bullish).\n"
        f"{'Analyst note: ' + qwen_reason if qwen_reason else ''}\n\n"
        f"Risk‑managed levels (use exactly these numbers):\n"
        f"- Area of Interest: {entry:.6f}\n"
        f"- Technical Invalidation: {stop:.6f} ({sl_pct:.2f}%)\n"
        f"- Target Objectives: {tp_str}\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. Start with a craving, scroll‑stopping hook that includes the cashtag and mentions a pattern or level.\n"
        "2. Write a detailed, multi‑paragraph analysis explaining what the candles are telling you – include candlestick patterns, "
        "support/resistance, and how the EMA/VWAP/volume confirm the bias.\n"
        "3. List the risk levels exactly as provided.\n"
        "4. Ask an open‑ended question that invites discussion.\n"
        "5. End with: #CryptoAnalysis #{ticker} #TechnicalAnalysis #BinanceSquare\n"
        "and the standard disclaimer."
    )

    def call_llama(sys_msg, usr_msg, temp=0.9):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": usr_msg}
            ],
            "temperature": temp,
            "max_tokens": 800
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except:
            pass
        return None

    text = call_llama(system_msg, user_prompt, 0.9)
    if not text or len(text) < 200:
        text = call_llama(system_msg, user_prompt, 1.0)

    if not text:
        hooks = [
            f"I've been watching ${ticker} closely – the technicals just lined up in a way I can't ignore. ⚡",
            f"${ticker} is printing a textbook {direction.lower()} structure on the 4‑hour chart. 🚀",
            f"Most traders are sleeping on ${ticker} right now, but the EMA20 just gave a clear signal. 🧐",
        ]
        hook = random.choice(hooks)
        direction_icon = "📈" if direction=="LONG" else "📉"
        text = (
            f"{hook} {direction_icon}\n\n"
            f"Trend is {'up' if layers['trend']>0 else 'down'}, momentum {'confirms' if layers['momentum']>0 else 'warns'}, "
            f"and volume is {'expanding' if layers['volume']>0 else 'stable'}. "
            f"The macro backdrop is {'supportive' if layers['macro_bias']>0 else 'cautious'} for altcoins.\n\n"
            f"🎯 Risk‑Managed Levels:\n"
            f"• Area of Interest: {entry:.6f}\n"
            f"• Technical Invalidation: {stop:.6f} ({sl_pct:.2f}%)\n"
            f"• Target Objectives: {tp_str}\n\n"
            f"What’s your game plan for ${ticker}? Are you entering now or waiting for a retest?\n"
            f"#CryptoAnalysis #{ticker} #TechnicalAnalysis #BinanceSquare\n"
            f"*Disclaimer: This analysis is for educational purposes only and does not constitute financial advice. Always DYOR.*"
        )
    return text.strip()

# ========== TRADE MANAGER ==========
def check_open_trades():
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
    except: return
    if open_df.empty: return
    if "timestamp" in open_df.columns: open_df = open_df.sort_values("timestamp").drop_duplicates("symbol", keep="last")
    else: open_df = open_df.drop_duplicates("symbol", keep="last")
    results = []; still_open = []; alerts = []
    now = datetime.now()
    fractions = [0.20, 0.20, 0.20, 0.20, 0.20]
    for _, trade in open_df.iterrows():
        try:
            sym = trade["symbol"]; direction = trade["action"]
            entry = float(trade["entry"]); stop_orig = float(trade["stop"])
            orig_qty = float(trade.get("original_qty", trade["quantity"]))
            remaining_qty = float(trade.get("quantity", orig_qty))
            highest_tp_idx = int(trade.get("highest_tp", -1))
            tps = []
            for i in range(1,6):
                tps.append(float(trade[f"TP{i}"]))
            # For trade checking, we can use 1h data from market_chart
            coin_id = COINGECKO_ID_MAP.get(sym, sym.replace("USDT","").lower())
            cg_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days=3"
            cg_data = fetch_coingecko(cg_url)
            if not cg_data or 'prices' not in cg_data:
                still_open.append(trade); continue
            prices = cg_data['prices']
            if len(prices) < 2:
                still_open.append(trade); continue
            df = pd.DataFrame(prices, columns=['time','price'])
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df.set_index('time', inplace=True)
            # Use high/low? Not directly available, so we'll estimate using price swings (not perfect but works)
            # Simpler: only check close price against TP/SL? That's fine for paper tracking.
            # We'll just check if price crossed levels by comparing consecutive prices.
            current_stop = entry if highest_tp_idx >= 0 else stop_orig
            full_close_data = None
            prev_price = None
            for ts, row in df.iterrows():
                p = row['price']
                if prev_price is not None:
                    # Approximate high/low as min/max of the two prices
                    high = max(p, prev_price)
                    low = min(p, prev_price)
                    new_tp_idx = None
                    if direction == "LONG":
                        for i in range(len(tps)-1, -1, -1):
                            if high >= tps[i] and i > highest_tp_idx:
                                new_tp_idx = i; break
                    else:
                        for i in range(len(tps)-1, -1, -1):
                            if low <= tps[i] and i > highest_tp_idx:
                                new_tp_idx = i; break
                    if new_tp_idx is not None:
                        # process partials (same as before)
                        for i in range(highest_tp_idx+1, new_tp_idx+1):
                            if remaining_qty <= 0: break
                            if i == 0:
                                current_stop = entry
                            fraction = fractions[i]
                            exit_qty = orig_qty * fraction
                            if exit_qty > remaining_qty: exit_qty = remaining_qty
                            if exit_qty > 0:
                                exit_price = tps[i]
                                pnl = (exit_price - entry) * exit_qty if direction=="LONG" else (entry - exit_price) * exit_qty
                                partial = trade.to_dict()
                                partial["hit_level"] = f"TP{i+1} (partial)"
                                partial["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                                partial["exit_price"] = exit_price
                                partial["quantity"] = exit_qty
                                partial["pnl_usdt"] = round(pnl,4)
                                results.append(partial)
                                update_portfolio({'pnl_usdt': pnl})
                                remaining_qty -= exit_qty
                                highest_tp_idx = i
                                alerts.append(f"🚀 {sym.replace('USDT','')} {direction} TP{i+1} hit — {fraction*100:.0f}% closed")
                            if i == 4:
                                if remaining_qty > 0:
                                    final_exit_qty = remaining_qty
                                    exit_price = tps[4]
                                    pnl = (exit_price - entry) * final_exit_qty if direction=="LONG" else (entry - exit_price) * final_exit_qty
                                    final = trade.to_dict()
                                    final["hit_level"] = "TP5 (final)"
                                    final["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                                    final["exit_price"] = exit_price
                                    final["quantity"] = final_exit_qty
                                    final["pnl_usdt"] = round(pnl,4)
                                    results.append(final)
                                    update_portfolio({'pnl_usdt': pnl})
                                    remaining_qty = 0
                                    highest_tp_idx = 4
                                    full_close_data = {
                                        "symbol": sym, "action": direction,
                                        "limit_price": entry, "stop_loss": stop_orig,
                                        "take_profits": tps
                                    }
                                    alerts.append(f"🔔 {sym.replace('USDT','')} {direction} TP5 hit — remaining closed")
                                break
                        if remaining_qty <= 0 and i == 4:
                            break
                    # Check stop loss
                    if remaining_qty > 0:
                        sl_hit = (low <= current_stop) if direction=="LONG" else (high >= current_stop)
                        if sl_hit:
                            exit_qty = remaining_qty; exit_price = current_stop
                            pnl = (exit_price - entry) * exit_qty if direction=="LONG" else (entry - exit_price) * exit_qty
                            final = trade.to_dict()
                            desc = "STOP LOSS" if highest_tp_idx == -1 else "BE STOP"
                            final["hit_level"] = desc
                            final["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                            final["exit_price"] = exit_price
                            final["quantity"] = exit_qty
                            final["pnl_usdt"] = round(pnl,4)
                            results.append(final)
                            update_portfolio({'pnl_usdt': pnl})
                            remaining_qty = 0
                            full_close_data = {
                                "symbol": sym, "action": direction,
                                "limit_price": entry, "stop_loss": stop_orig,
                                "take_profits": tps
                            }
                            alerts.append(f"🔴 {sym.replace('USDT','')} {direction} → {desc} (remaining closed)")
                            break
                prev_price = p
            if remaining_qty > 0:
                trade["quantity"] = remaining_qty
                trade["highest_tp"] = highest_tp_idx
                still_open.append(trade)
            elif full_close_data:
                send_trade_chart(full_close_data, title_suffix=f" – Closed")
        except Exception as e:
            print(f"Trade check error {trade['symbol']}: {e}")
            still_open.append(trade)
    if results:
        dfr = pd.DataFrame(results); append_csv(TRADE_RESULTS_CSV, dfr)
    if still_open:
        dfs = pd.DataFrame(still_open); save_csv(OPEN_TRADES_CSV, dfs); portfolio['open_positions'] = len(dfs)
    else:
        save_csv(OPEN_TRADES_CSV, pd.DataFrame()); portfolio['open_positions'] = 0
    save_portfolio(portfolio)
    if alerts: send_telegram("\n".join(alerts))

# ========== SIGNAL GENERATION ==========
def generate_signal(balance_usdt):
    try:
        coins_data = fetch_coingecko("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1")
        if not coins_data: return {"action":"HOLD","reasoning":"CoinGecko unavailable.","summary":""}
        open_symbols = set()
        risky = 0
        try:
            odf = pd.read_csv(OPEN_TRADES_CSV)
            if not odf.empty:
                odf = odf.sort_values("timestamp").drop_duplicates("symbol",keep="last") if "timestamp" in odf.columns else odf.drop_duplicates("symbol",keep="last")
                open_symbols = set(odf["symbol"])
                risky = sum(odf["highest_tp"] == -1) if "highest_tp" in odf.columns else len(odf)
        except: pass
        if risky >= 3: return {"action":"HOLD","reasoning":f"Max 3 risky trades ({risky}).","summary":""}
        candidates = []
        for c in coins_data:
            sym = c.get("symbol","").upper()+"USDT"
            price = c.get("current_price")
            if price and price>0 and sym not in open_symbols:
                candidates.append({"symbol":sym,"price":price,"volume":c.get("total_volume",0)})
        candidates.sort(key=lambda x: x["volume"], reverse=True)
        candidates = candidates[:50]
        if not candidates: return {"action":"HOLD","reasoning":"No liquid coins.","summary":""}
        macro_bias = get_macro_bias()
        coin_data_cache = {}
        btc_df = get_4h_klines("BTCUSDT", days=14)
        all_scored = []
        for coin in candidates:
            total, layers, trend_dir, atr = score_coin(coin["symbol"], coin["price"], coin["volume"], macro_bias, coin_data_cache, btc_df)
            coin["score"] = total; coin["atr"] = atr; coin["layers"] = layers; coin["trend_dir"] = trend_dir
            all_scored.append(coin)
        if not all_scored: return {"action":"HOLD","reasoning":"No valid scores.","summary":""}
        all_scored_sorted = sorted(all_scored, key=lambda x: abs(x["score"]), reverse=True)
        summary = " | ".join([f"{c['symbol'].replace('USDT','')}: {c['score']:.2f}" for c in all_scored_sorted[:30]])
        top5 = all_scored_sorted[:5]
        best_combined = -999
        best_signal = None
        for coin in top5:
            if abs(coin["score"]) < 0.5: continue
            direction = "LONG" if coin["score"] >= 0 else "SHORT"
            rating, reason = evaluate_deep(coin, direction, macro_bias)
            combined = abs(coin["score"]) * (rating / 5.0)
            if combined > best_combined:
                best_combined = combined
                coin["direction"] = direction
                coin["rating"] = rating
                coin["llama_reason"] = reason
                best_signal = coin
        if best_signal is None or best_combined < 1.49:
            best_all = all_scored_sorted[0] if all_scored_sorted else None
            reason = ""
            if best_all:
                layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_all["layers"].items()])
                reason = (
                    f"No strong conviction. Best internal score: {best_all['score']:.2f} for {best_all['symbol']}.\n"
                    f"Llama filter active – no candidate passed the quality check.\n"
                    f"Layers: {layer_str}\n"
                    f"Top coins: {summary}"
                )
            else:
                reason = "No valid coins to evaluate."
            return {"action":"HOLD", "reasoning": reason, "summary": summary}
        coin = best_signal
        direction = coin["direction"]
        entry = coin.get("bid", coin["price"]*0.999) if direction=="LONG" else coin.get("ask", coin["price"]*1.001)
        atr = coin["atr"]
        # Confirm with 1h candle direction (using CoinGecko market_chart)
        coin_id = COINGECKO_ID_MAP.get(coin["symbol"], coin["symbol"].replace("USDT","").lower())
        cg_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days=1"
        cg_data = fetch_coingecko(cg_url)
        confirm = False
        if cg_data and 'prices' in cg_data and len(cg_data['prices']) >= 2:
            p1 = cg_data['prices'][-2][1]
            p2 = cg_data['prices'][-1][1]
            if direction == "LONG" and p2 > p1: confirm = True
            elif direction == "SHORT" and p2 < p1: confirm = True
        stop_mult = 1.2 if confirm else 1.5
        min_stop = max(stop_mult * atr, entry * 0.01)
        stop = entry - min_stop if direction=="LONG" else entry + min_stop
        risk_per_share = abs(entry - stop)
        qty = round((balance_usdt * 0.01) / risk_per_share, 6)
        mults = [0.5, 1.0, 2.0, 3.0, 5.0]
        tps = []
        for m in mults:
            if direction == "LONG":
                tps.append(round(entry + m * risk_per_share, 6))
            else:
                tps.append(round(entry - m * risk_per_share, 6))
        sl_pct = abs(entry - stop)/entry*100
        post = generate_post(coin, direction, entry, stop, tps, sl_pct, qwen_reason=coin.get("llama_reason",""))
        return {
            "action": direction,
            "symbol": coin["symbol"],
            "quantity": qty,
            "limit_price": entry,
            "stop_loss": stop,
            "take_profits": tps,
            "confidence_score": compute_confidence(coin["layers"]),
            "conviction_score": abs(coin["score"]),
            "post_text": post,
            "summary": summary
        }
    except Exception as e:
        print(f"generate_signal error: {e}")
        return {"action":"HOLD","reasoning":f"Internal error: {e}","summary":""}

# ========== DARK CHART ==========
def send_trade_chart(signal, title_suffix=""):
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; import mplfinance as mpf
        sym = signal['symbol']; df = get_4h_klines(sym, days=10)
        if df.empty: return
        style = mpf.make_mpf_style(base_mpf_style='nightclouds', facecolor='#000000', gridcolor='#2a2e39',
                                   rc={'axes.labelcolor':'white','xtick.color':'white','ytick.color':'white','axes.titlecolor':'white'})
        ema50 = df['Close'].ewm(span=50, adjust=False).mean()
        apds = [mpf.make_addplot(ema50,color='#f39c12',width=1.5,label='EMA50')]
        fig,axes=mpf.plot(df,type='candle',style=style,title=f"{sym.replace('USDT','')} 4h{title_suffix}",ylabel='Price',addplot=apds,returnfig=True,figsize=(8,6))
        ax=axes[0]
        entry=signal.get('limit_price'); stop=signal.get('stop_loss'); tps=signal.get('take_profits')
        if entry and stop:
            ax.axhline(y=entry,color='#f1c40f',linestyle='--',linewidth=1.5,label='Entry')
            ax.axhline(y=stop,color='#e74c3c',linestyle='--',linewidth=1.5,label='Stop')
            if tps:
                labels = ['TP1 (0.5R)', 'TP2 (1R)', 'TP3 (2R)', 'TP4 (3R)', 'TP5 (5R)']
                for i,tp in enumerate(tps):
                    ax.axhline(y=tp,color='#2ecc71',linestyle='--',linewidth=1,alpha=0.8,label=labels[i])
            ax.legend(loc='upper left',facecolor='#000000',edgecolor='white',labelcolor='white')
        path=f"{sym.replace('USDT','')}_chart.png"
        fig.savefig(path,dpi=150,bbox_inches='tight',facecolor='black'); plt.close(fig)
        url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(path,'rb') as img: requests.post(url,data={'chat_id':CHAT_ID},files={'photo':img})
        os.remove(path)
    except ImportError:
        base=signal['symbol'].replace("USDT","").upper(); studies="&studies[]=STD%3BEMA%3B50&studies[]=STD%3BVWAP"
        send_telegram(f"📈 Chart with EMA & VWAP: https://www.tradingview.com/chart/?symbol=BINANCE:{base}USDT&interval=240{studies}")
    except Exception as e: print(f"Chart error: {e}")

def send_telegram(text):
    url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url,data={"chat_id":CHAT_ID,"text":text},timeout=10)
    except Exception as e: print("TG error:",e)

def main():
    try:
        initialize_trade_files()
        check_open_trades()
        if get_daily_pnl() <= portfolio['daily_loss_limit']:
            send_telegram(f"Daily loss limit reached (PnL: {get_daily_pnl():.2f} USD). No new trades.")
            return
        dec = generate_signal(portfolio['balance_usdt'])
        if dec['action'] in ["LONG","SHORT"]:
            log_signal(dec); add_open_trade(dec); portfolio['open_positions'] += 1; save_portfolio(portfolio)
            send_telegram(dec['post_text'])
            send_trade_chart(dec)
        else:
            send_telegram(dec.get('reasoning','HOLD'))
    except Exception as e:
        err = f"Bot crashed: {traceback.format_exc()}"
        print(err); send_telegram(err[:500])

if __name__ == "__main__":
    main()