import requests, json, os, traceback, re, time
import pandas as pd
import numpy as np
import yfinance as yf
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

# ========== FULL UNIVERSE (deduplicated – removed delisted coins) ==========
COIN_LIST = list(set([
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT",
    "SOLUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "LTCUSDT", "NEARUSDT", "ATOMUSDT", "ETCUSDT",
    "STXUSDT", "FILUSDT", "ARBUSDT", "OPUSDT", "INJUSDT",
    "TIAUSDT", "SEIUSDT", "RUNEUSDT", "GRTUSDT", "AAVEUSDT",
    "ALGOUSDT", "SANDUSDT", "MANAUSDT", "THETAUSDT", "FTMUSDT",
    "EOSUSDT", "MKRUSDT", "LDOUSDT", "IMXUSDT", "FLOWUSDT",
    "XTZUSDT", "NEOUSDT", "KSMUSDT", "ZECUSDT", "DASHUSDT",
    "EGLDUSDT", "MINAUSDT", "GALAUSDT", "HNTUSDT", "CFXUSDT",
    "ARUSDT", "FETUSDT", "AGIXUSDT", "OCEANUSDT", "1INCHUSDT",
    "CRVUSDT", "AXSUSDT", "CHZUSDT", "ENJUSDT", "BATUSDT",
    "SNXUSDT", "COMPUSDT", "YFIUSDT", "SUSHIUSDT", "ZRXUSDT",
    "RENUSDT", "CELOUSDT", "LRCUSDT", "ANKRUSDT", "STORJUSDT",
    "COTIUSDT", "KAVAUSDT", "ICXUSDT", "ONTUSDT", "ZILUSDT",
    "WAVESUSDT", "QTUMUSDT", "OMGUSDT", "BANDUSDT", "DENTUSDT",
    "HOTUSDT", "IOSTUSDT", "RVNUSDT", "SCUSDT", "ZENUSDT",
    "CKBUSDT", "SKLUSDT", "CTSIUSDT", "CTKUSDT", "LINAUSDT",
    "TRBUSDT", "BALUSDT", "PERPUSDT", "BNTUSDT", "RSRUSDT",
    "TOMOUSDT", "DGBUSDT", "DUSKUSDT", "REEFUSDT", "ALPHAUSDT",
    "FORTHUSDT", "POLSUSDT", "C98USDT", "RAREUSDT", "ATAUSDT",
    "IDEXUSDT", "MLNUSDT",
    "WIFUSDT", "BONKUSDT", "FLOKIUSDT"
]))

# ========== CSV FILE PATHS ==========
TRADE_LOG_CSV = "trade_log.csv"
OPEN_TRADES_CSV = "open_trades.csv"
TRADE_RESULTS_CSV = "trade_results.csv"

# ========== DATA HELPERS ==========
def fetch_coingecko(url, retries=2):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:     # rate limited
                time.sleep(2 ** attempt)
        except:
            time.sleep(1)
    return None

def get_yahoo_klines(symbol_usdt, interval='4h', days=60, start=None, end=None):
    yahoo_symbol = symbol_usdt.replace("USDT", "-USD")
    if start is None:
        end = datetime.now()
        start = end - timedelta(days=days)
    else:
        if end is None:
            end = datetime.now()
    try:
        df = yf.download(yahoo_symbol, start=start, end=end, interval=interval, progress=False)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except:
        return pd.DataFrame()

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
                               "TP1", "TP2", "TP3", "TP4", "TP5", "status", "quantity", "highest_tp"])
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
        "TP1": signal["take_profits"][0],
        "TP2": signal["take_profits"][1],
        "TP3": signal["take_profits"][2],
        "TP4": signal["take_profits"][3],
        "TP5": signal["take_profits"][4],
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
        "highest_tp": -1
    }
    df = pd.DataFrame([row])
    append_csv(OPEN_TRADES_CSV, df)

# ========== PORTFOLIO HELPERS ==========
def get_daily_pnl():
    """Calculate today's realized PnL from trade_results.csv."""
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
    portfolio['open_positions'] -= 1
    save_portfolio(portfolio)

# ========== INSTITUTIONAL IMPROVEMENTS ==========

def institutional_macro_filter():
    df_btc = get_yahoo_klines("BTCUSDT", interval='4h', days=14)
    if df_btc.empty or len(df_btc) < 50:
        return 0
    closes = df_btc['Close']
    ema50 = closes.ewm(span=50, adjust=False).mean()
    current = closes.iloc[-1]
    btc_bullish = current > ema50.iloc[-1]

    try:
        data = fetch_coingecko("https://api.coingecko.com/api/v3/global")
        if data and 'data' in data:
            dom = data['data'].get('market_cap_percentage', {}).get('usdt', 0)
            usdt_dom = dom
        else:
            usdt_dom = 5
    except:
        usdt_dom = 5

    macro_score = 0
    if btc_bullish:
        macro_score += 1
        if usdt_dom < 4.5:
            macro_score += 1
    else:
        macro_score -= 1
        if usdt_dom > 6.5:
            macro_score -= 1

    return max(-2, min(2, macro_score))

def anchored_vwap_score(df, current_price):
    if len(df) < 50:
        return 0
    df = df.copy()
    typical = (df['High'] + df['Low'] + df['Close']) / 3
    df['vpv'] = typical * df['Volume']
    total_vol = df['Volume'].sum()
    if total_vol == 0:
        return 0
    vwap = df['vpv'].sum() / total_vol
    deviation = (current_price - vwap) / vwap * 100
    if deviation > 1:
        return 1
    elif deviation < -1:
        return -1
    else:
        return 0

def refined_buying_pressure(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=10)
    if df.empty or len(df) < 48:
        return 0, 0
    short = df.tail(12)
    buy_vol_s = short.loc[short['Close'] > short['Open'], 'Volume'].sum()
    sell_vol_s = short.loc[short['Close'] <= short['Open'], 'Volume'].sum()
    total_s = buy_vol_s + sell_vol_s
    short_press = (buy_vol_s - sell_vol_s) / total_s if total_s > 0 else 0

    long_df = df.tail(48)
    buy_vol_l = long_df.loc[long_df['Close'] > long_df['Open'], 'Volume'].sum()
    sell_vol_l = long_df.loc[long_df['Close'] <= long_df['Open'], 'Volume'].sum()
    total_l = buy_vol_l + sell_vol_l
    long_press = (buy_vol_l - sell_vol_l) / total_l if total_l > 0 else 0

    return short_press, long_press

# ========== TRAILING STOP LOGIC ==========

def update_stop(direction, tp_index, entry, tps):
    if direction == "LONG":
        return entry if tp_index == 0 else tps[tp_index - 1]
    else:
        return entry if tp_index == 0 else tps[tp_index - 1]

def resolve_ambiguous(sym, direction, entry, current_stop, tps, start_highest,
                      start_time, end_time, depth=0):
    if depth == 0:
        interval = '15m'
    elif depth == 1:
        interval = '5m'
    else:
        return resolve_heuristic(sym, direction, entry, current_stop, tps, start_highest,
                                 start_time, end_time)

    df = get_yahoo_klines(sym, interval=interval, start=start_time, end=end_time)
    if df.empty:
        return resolve_heuristic(sym, direction, entry, current_stop, tps, start_highest,
                                 start_time, end_time)

    highest = start_highest
    temp_stop = current_stop

    for _, candle in df.iterrows():
        high, low, open_, close_ = candle['High'], candle['Low'], candle['Open'], candle['Close']
        is_bullish = close_ > open_

        new_tp = None
        if direction == "LONG":
            for i in range(len(tps)-1, -1, -1):
                if high >= tps[i] and i > highest:
                    new_tp = i
                    break
        else:
            for i in range(len(tps)-1, -1, -1):
                if low <= tps[i] and i > highest:
                    new_tp = i
                    break

        sl_hit = (low <= temp_stop) if direction == "LONG" else (high >= temp_stop)

        if new_tp is not None and sl_hit:
            if depth < 2:
                sub_out, sub_exit = resolve_ambiguous(
                    sym, direction, entry, temp_stop, tps, highest,
                    candle.name, candle.name + pd.Timedelta(hours=1) if interval=='15m' else candle.name + pd.Timedelta(minutes=15),
                    depth+1
                )
                return sub_out, sub_exit
            else:
                return resolve_heuristic_decision(direction, is_bullish, new_tp, temp_stop, tps, highest)

        if new_tp is not None and not sl_hit:
            highest = new_tp
            temp_stop = update_stop(direction, highest, entry, tps)
            if direction == "LONG" and low <= temp_stop:
                return f"TP{highest+1}", temp_stop
            if direction == "SHORT" and high >= temp_stop:
                return f"TP{highest+1}", temp_stop
            continue

        if sl_hit and new_tp is None:
            return (f"TP{highest+1}" if highest >= 0 else "STOP LOSS"), temp_stop

    return None, None

def resolve_heuristic(sym, direction, entry, current_stop, tps, start_highest, start_time, end_time):
    df = get_yahoo_klines(sym, interval='1h', start=start_time, end=end_time)
    if df.empty:
        return "STOP LOSS", current_stop
    candle = df.iloc[0]
    is_bullish = candle['Close'] > candle['Open']
    high, low = candle['High'], candle['Low']

    new_tp = None
    if direction == "LONG":
        for i in range(len(tps)-1, -1, -1):
            if high >= tps[i] and i > start_highest:
                new_tp = i
                break
    else:
        for i in range(len(tps)-1, -1, -1):
            if low <= tps[i] and i > start_highest:
                new_tp = i
                break

    if new_tp is None:
        return "STOP LOSS", current_stop
    return resolve_heuristic_decision(direction, is_bullish, new_tp, current_stop, tps, start_highest)

def resolve_heuristic_decision(direction, is_bullish, new_tp, current_stop, tps, start_highest):
    if direction == "LONG":
        if is_bullish:
            new_stop = update_stop(direction, new_tp, None, tps)
            return f"TP{new_tp+1}", new_stop
        else:
            return "STOP LOSS", current_stop
    else:
        if not is_bullish:
            new_stop = update_stop(direction, new_tp, None, tps)
            return f"TP{new_tp+1}", new_stop
        else:
            return "STOP LOSS", current_stop

def check_open_trades():
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return

    if open_df.empty:
        return

    if "highest_tp" not in open_df.columns:
        open_df["highest_tp"] = -1
    if "quantity" not in open_df.columns:
        open_df["quantity"] = 0.0

    results = []
    still_open = []
    alerts = []
    tp_alerts = []
    now = datetime.now()
    mults = [0.4, 0.8, 1.2, 1.6, 2.0]

    for idx, trade in open_df.iterrows():
        sym = trade["symbol"]
        direction = trade["action"]
        entry = float(trade["entry"])
        stop_orig = float(trade["stop"])
        qty = float(trade.get("quantity", 0))
        risk = abs(entry - stop_orig)

        tps = []
        for m in mults:
            if direction == "LONG":
                tps.append(entry + m * risk)
            else:
                tps.append(entry - m * risk)

        try:
            entry_time = datetime.strptime(trade["timestamp"], "%Y-%m-%d %H:%M:%S")
        except:
            still_open.append(trade)
            continue

        df_1h = get_yahoo_klines(sym, interval='1h', start=entry_time, end=now)
        if df_1h.empty:
            still_open.append(trade)
            continue

        current_stop = stop_orig
        highest_tp_idx = int(trade.get("highest_tp", -1))
        if highest_tp_idx >= 0:
            current_stop = update_stop(direction, highest_tp_idx, entry, tps)

        outcome = None
        exit_price = None
        new_high = highest_tp_idx
        atr_4h = None

        for candle_time, candle in df_1h.iterrows():
            high = candle['High']
            low = candle['Low']
            open_ = candle['Open']
            close_ = candle['Close']

            new_tp_idx = None
            if direction == "LONG":
                for i in range(len(tps)-1, -1, -1):
                    if high >= tps[i] and i > highest_tp_idx:
                        new_tp_idx = i
                        break
            else:
                for i in range(len(tps)-1, -1, -1):
                    if low <= tps[i] and i > highest_tp_idx:
                        new_tp_idx = i
                        break

            sl_touched = (low <= current_stop) if direction == "LONG" else (high >= current_stop)

            if new_tp_idx is not None and sl_touched:
                sub_outcome, sub_exit = resolve_ambiguous(
                    sym, direction, entry, current_stop, tps, highest_tp_idx,
                    candle_time, candle_time + timedelta(hours=1)
                )
                if sub_outcome:
                    outcome = sub_outcome
                    exit_price = sub_exit
                    break
                continue

            if new_tp_idx is not None and not sl_touched:
                highest_tp_idx = new_tp_idx
                new_high = highest_tp_idx
                current_stop = update_stop(direction, highest_tp_idx, entry, tps)

                if highest_tp_idx >= 1:
                    if atr_4h is None:
                        atr_4h, _ = get_4h_atr(sym, entry)
                    if direction == "LONG":
                        trail_stop = high - 1.5 * atr_4h
                        current_stop = max(current_stop, trail_stop)
                    else:
                        trail_stop = low + 1.5 * atr_4h
                        current_stop = min(current_stop, trail_stop)

                if highest_tp_idx == 0:
                    stop_desc = "BE"
                else:
                    stop_desc = f"TP{highest_tp_idx} (dynamic trail)"
                tp_alerts.append(f"🚀 {sym.replace('USDT','')} {direction} TP{highest_tp_idx+1} hit — SL moved to {stop_desc}")

                if direction == "LONG" and low <= current_stop:
                    outcome = f"TP{highest_tp_idx+1}"
                    exit_price = current_stop
                    break
                if direction == "SHORT" and high >= current_stop:
                    outcome = f"TP{highest_tp_idx+1}"
                    exit_price = current_stop
                    break
                continue

            if sl_touched and new_tp_idx is None:
                outcome = f"TP{highest_tp_idx+1}" if highest_tp_idx >= 0 else "STOP LOSS"
                exit_price = current_stop
                break

        if outcome is None:
            trade["highest_tp"] = new_high
            still_open.append(trade)
            continue

        if direction == "LONG":
            pnl_usdt = qty * (exit_price - entry)
        else:
            pnl_usdt = qty * (entry - exit_price)

        result = trade.to_dict()
        result["hit_level"] = outcome
        result["close_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
        result["exit_price"] = exit_price
        result["pnl_usdt"] = round(pnl_usdt, 4)
        results.append(result)

        update_portfolio({
            'pnl_usdt': pnl_usdt,
            'quantity': qty,
            'direction': direction,
            'entry': entry,
            'exit_price': exit_price
        })

        pnl_pct = (exit_price - entry) / entry * 100 if direction == "LONG" else (entry - exit_price) / entry * 100
        icon = "🔔" if "TP" in outcome else "🔴"
        alerts.append(f"{icon} {sym.replace('USDT','')} {direction} → {outcome} ({pnl_pct:+.2f}%)")

    if results:
        df_results = pd.DataFrame(results)
        append_csv(TRADE_RESULTS_CSV, df_results)

    if still_open:
        df_still_open = pd.DataFrame(still_open)
        if "highest_tp" not in df_still_open.columns:
            df_still_open["highest_tp"] = -1
        if "quantity" not in df_still_open.columns:
            df_still_open["quantity"] = 0.0
        portfolio['open_positions'] = len(df_still_open)
        save_csv(OPEN_TRADES_CSV, df_still_open)
    else:
        portfolio['open_positions'] = 0
        save_csv(OPEN_TRADES_CSV, pd.DataFrame())
    save_portfolio(portfolio)

    if tp_alerts:
        send_telegram("\n".join(tp_alerts))
    if alerts:
        msg = "Trade updates:\n" + "\n".join(alerts)
        send_telegram(msg)

# ========== 4‑HOUR ANALYSIS ==========
def get_technicals(symbol_usdt):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=14)
    error = None
    if df.empty or len(df) < 50:
        error = f"insufficient 4h data ({len(df)} candles)"
        return {
            "trend": 0, "adx": 0, "structure": 0,
            "combined": 0, "ema50_distance": 1.0, "error": error
        }
    closes = df['Close']
    highs = df['High']
    lows = df['Low']
    ema50 = closes.ewm(span=50, adjust=False).mean()
    ema200 = closes.ewm(span=200, adjust=False).mean() if len(closes) >= 200 else ema50
    current = closes.iloc[-1]
    trend = 0
    if current > ema50.iloc[-1]:
        trend += 1.5
    else:
        trend -= 1.5
    if ema50.iloc[-1] > ema200.iloc[-1]:
        trend += 1.5
    else:
        trend -= 1.5
    trend = max(-3, min(3, trend))

    def calc_adx(high, low, close, period=14):
        dm_plus = high.diff()
        dm_minus = -low.diff()
        dm_plus[dm_plus < 0] = 0
        dm_minus[dm_minus < 0] = 0
        tr = pd.concat([high - low,
                        (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1/period, adjust=False).mean()
        di_plus = 100 * (dm_plus.ewm(alpha=1/period, adjust=False).mean() / atr)
        di_minus = 100 * (dm_minus.ewm(alpha=1/period, adjust=False).mean() / atr)
        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
        adx = dx.ewm(alpha=1/period, adjust=False).mean()
        return adx, di_plus, di_minus

    adx_series, di_plus, di_minus = calc_adx(highs, lows, closes, 14)
    adx_now = adx_series.iloc[-1]
    di_plus_now = di_plus.iloc[-1]
    di_minus_now = di_minus.iloc[-1]

    adx_score = 0
    if adx_now > 25:
        if di_plus_now > di_minus_now:
            adx_score = 2.5
        else:
            adx_score = -2.5
    elif adx_now > 20:
        if di_plus_now > di_minus_now:
            adx_score = 1.0
        else:
            adx_score = -1.0

    window = 7
    lookback = min(50, len(highs))
    recent_highs = highs.iloc[-lookback:]
    recent_lows  = lows.iloc[-lookback:]
    swing_highs = []
    swing_lows  = []
    for i in range(window, len(recent_highs) - window):
        if all(recent_highs.iloc[i] >= recent_highs.iloc[i-window:i+window+1]):
            swing_highs.append((i, recent_highs.iloc[i]))
        if all(recent_lows.iloc[i] <= recent_lows.iloc[i-window:i+window+1]):
            swing_lows.append((i, recent_lows.iloc[i]))

    structure_score = 0
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        last_hh = swing_highs[-1][1] > swing_highs[-2][1]
        last_hl = swing_lows[-1][1] > swing_lows[-2][1]
        if last_hh and last_hl:
            if len(swing_highs) >= 3 and len(swing_lows) >= 3:
                prev_hh = swing_highs[-2][1] > swing_highs[-3][1]
                prev_hl = swing_lows[-2][1] > swing_lows[-3][1]
                if prev_hh and prev_hl:
                    structure_score = 3.0
                else:
                    structure_score = 2.0
            else:
                structure_score = 2.0
        elif (not last_hh) and (not last_hl):
            if len(swing_highs) >= 3 and len(swing_lows) >= 3:
                prev_lh = swing_highs[-2][1] < swing_highs[-3][1]
                prev_ll = swing_lows[-2][1] < swing_lows[-3][1]
                if prev_lh and prev_ll:
                    structure_score = -3.0
                else:
                    structure_score = -2.0
            else:
                structure_score = -2.0
    structure_score = max(-3, min(3, structure_score))

    combined = (
        trend * 0.30 +
        adx_score * 0.25 +
        structure_score * 0.45
    )
    ema50_val = ema50.iloc[-1]
    distance_pct = abs(current - ema50_val) / current
    trend_dir = "up" if current > ema50.iloc[-1] else "down"
    return {
        "trend": trend, "adx": adx_score, "structure": structure_score,
        "combined": combined, "ema50_distance": distance_pct,
        "adx_value": adx_now, "trend_dir": trend_dir, "error": None
    }

def get_4h_atr(symbol_usdt, current_price):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=14)
    if df.empty or len(df) < 14:
        return current_price * 0.02, "ATR data insufficient, using 2% fallback"
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    if pd.isna(atr):
        return current_price * 0.02, "ATR calculation failed, using 2% fallback"
    return atr, None

def get_buying_pressure(symbol_usdt):
    short_p, long_p = refined_buying_pressure(symbol_usdt)
    if short_p * long_p > 0:
        score = (short_p + long_p) / 2 * 3
    else:
        score = (short_p + long_p) / 2 * 3 * 0.3
    return score, None

def get_volatility_score(symbol_usdt, current_price):
    atr, atr_err = get_4h_atr(symbol_usdt, current_price)
    atr_pct = atr / current_price * 100
    if atr_pct < 2 or atr_pct > 7:
        return -1, atr_err
    return 1, None

def btc_trend_score():
    df = get_yahoo_klines("BTCUSDT", interval='4h', days=14)
    if df.empty or len(df) < 50:
        return 0, "BTC data unavailable"
    closes = df['Close']
    ema50 = closes.ewm(span=50, adjust=False).mean()
    current = closes.iloc[-1]
    if current > ema50.iloc[-1]:
        return 2, None
    else:
        return -2, None

def volume_trend_score(symbol_usdt, direction=None):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=5)
    if df.empty or len(df) < 12:
        return 0, f"volume data insufficient ({len(df)} candles)"
    recent = df['Volume'].tail(6)
    first_half = recent[:3].mean()
    second_half = recent[3:].mean()
    if second_half > first_half * 1.05:
        if direction == "down":
            return -2, None
        return 2, None
    elif second_half < first_half * 0.95:
        if direction == "up":
            return -2, None
        return -2, None
    return 0, None

def momentum_alignment_score(symbol_usdt, direction, layers):
    df = get_yahoo_klines(symbol_usdt, interval='4h', days=2)
    if df.empty or len(df) < 2:
        return 0.0
    last = df.iloc[-1]
    candle_agrees = (direction == "LONG" and last['Close'] > last['Open']) or \
                    (direction == "SHORT" and last['Close'] < last['Open'])
    if not candle_agrees:
        return 0.0
    supporting = 0
    if direction == "LONG":
        if layers.get("buying_press", 0) > 0.5: supporting += 1
        if layers.get("intermarket", 0) > 0.5: supporting += 1
        if layers.get("volume_trend", 0) > 0.5: supporting += 1
    else:
        if layers.get("buying_press", 0) < -0.5: supporting += 1
        if layers.get("intermarket", 0) < -0.5: supporting += 1
        if layers.get("volume_trend", 0) < -0.5: supporting += 1
    if supporting >= 2:
        return 0.20 if direction == "LONG" else -0.20
    return 0.0

def trend_strength_bonus(adx_value, base_score):
    if adx_value > 35 and abs(base_score) > 0.5:
        return 0.30 * (1 if base_score > 0 else -1)
    elif adx_value > 30 and abs(base_score) > 0.5:
        return 0.20 * (1 if base_score > 0 else -1)
    return 0.0

# ========== SCORING ENGINE ==========
def score_coin(symbol, price, volume_24h, change1h, btc_score, btc_error, macro_score):
    errors = []
    tech = get_technicals(symbol)
    if tech.get("error"):
        errors.append(f"tech({symbol}): {tech['error']}")
    tech_combined = tech["combined"]
    ema50_distance = tech["ema50_distance"]
    adx_value = tech.get("adx_value", 0)
    trend_dir = tech.get("trend_dir", "up")

    buying_score, buy_err = get_buying_pressure(symbol)
    if buy_err:
        errors.append(f"buying_press({symbol}): {buy_err}")

    vol_score, vol_err = get_volatility_score(symbol, price)
    if vol_err:
        errors.append(f"volatility({symbol}): {vol_err}")

    intermarket_s = btc_score
    if btc_error:
        errors.append(f"intermarket: {btc_error}")

    vol_trend_s, vt_err = volume_trend_score(symbol, direction=trend_dir)
    if vt_err:
        errors.append(f"volume_trend({symbol}): {vt_err}")

    df_vwap = get_yahoo_klines(symbol, interval='4h', days=14)
    vwap_score = anchored_vwap_score(df_vwap, price)

    total = (
        0.20 * tech_combined +
        0.45 * buying_score +
        0.05 * vol_score +
        0.25 * intermarket_s +
        0.05 * vol_trend_s
    )

    macro_multiplier = 1 + 0.15 * macro_score
    total *= macro_multiplier

    total += vwap_score * 0.1

    layers = {
        "tech": tech_combined,
        "buying_press": buying_score,
        "volatility": vol_score,
        "intermarket": intermarket_s,
        "volume_trend": vol_trend_s,
    }
    return max(-3, min(3, total)), layers, ema50_distance, adx_value, trend_dir, errors

# ========== AI REASONING ==========
def call_groq_reasoning(symbol, entry, atr, layers, errors=None):
    layer_str = "; ".join([f"{k}={v:.2f}" for k,v in layers.items()])
    err_str = ""
    if errors:
        err_str = " | Data issues: " + "; ".join(errors)

    directional_scores = [layers["tech"], layers["buying_press"], layers["intermarket"], layers["volume_trend"]]
    bearish_count = sum(1 for s in directional_scores if s < -0.5)
    bullish_count = sum(1 for s in directional_scores if s > 0.5)
    alignment_strength = max(bearish_count, bullish_count)

    system_msg = (
        "You are a senior institutional crypto trader. "
        "Given a trade signal, you write a concise market note in three short parts:\n"
        "- Market structure: what is happening on the chart (trend, support/resistance, breakout)\n"
        "- Catalyst: what is driving the move (volume, BTC correlation, sentiment)\n"
        "- Risk note: key invalidation level or warning\n"
        "Write each part as one plain sentence. No indicators, no numbers, no mention of 'layers' or 'alignment'. "
        "Use professional, direct language. Keep the entire note under 150 characters."
    )

    user_prompt = (
        f"Trade signal for {symbol} at {entry}. 4h ATR: {atr:.4f}. "
        f"Internal metrics: {layer_str}{err_str}. "
        f"Alignment: {alignment_strength}/4 directional metrics are strongly aligned (bullish/bearish). "
        "Produce a confidence score between 4 and 7 (5 if 2 metrics align, 6 if 3, 7 if 4). "
        "Then provide the reasoning in the format:\n"
        "CONFIDENCE: 7 | MARKET STRUCTURE: [text] | CATALYST: [text] | RISK NOTE: [text]"
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.6,
        "max_tokens": 180
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"]
            conf_match = re.search(r'CONFIDENCE:\s*(\d+)', text)
            reason_text = text.split("|", 1)[1].strip() if "|" in text else text
            conf = int(conf_match.group(1)) if conf_match else 5
            conf = max(4, min(7, conf))
            return conf, reason_text
    except:
        pass
    return 5, "Market structure: neutral. Catalyst: none. Risk note: automated signal."

# ========== SIGNAL GENERATION ==========
def generate_signal(balance_usdt):
    cg_url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1"
    coins_data = fetch_coingecko(cg_url)
    if not coins_data:
        return {"action": "HOLD", "reasoning": "CoinGecko market data unavailable."}

    open_symbols = set()
    try:
        open_df = pd.read_csv(OPEN_TRADES_CSV)
        if not open_df.empty:
            open_symbols = set(open_df["symbol"].values)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass

    cg_map = {}
    for coin in coins_data:
        sym = coin.get("symbol", "").upper() + "USDT"
        if coin.get("current_price", 0) > 0:
            cg_map[sym] = {"price": coin["current_price"], "volume": coin.get("total_volume", 0)}

    candidates = []
    for sym in COIN_LIST:
        if sym in open_symbols:
            continue
        if sym not in cg_map:
            continue
        candidates.append({"symbol": sym, "price": cg_map[sym]["price"], "volume": cg_map[sym]["volume"]})
    candidates.sort(key=lambda x: x["volume"], reverse=True)
    candidates = candidates[:60]

    if not candidates:
        return {"action": "HOLD", "reasoning": "No liquid coins available (all coins with open trades skipped)."}

    btc_score, btc_error = btc_trend_score()
    macro_score = institutional_macro_filter()

    all_scored = []
    best = None
    best_score = 0
    best_layers = None
    best_ema_distance = 0.0
    best_adx = 0
    best_trend_dir = None
    best_errors = []

    for coin in candidates:
        sym = coin["symbol"]
        price = coin["price"]
        volume = coin["volume"]

        total_score, layers, ema_dist, adx_val, trend_dir, errors = score_coin(
            sym, price, volume, 0, btc_score, btc_error, macro_score
        )
        atr, _ = get_4h_atr(sym, price)
        if atr / price > 0.07:
            total_score = 0.0
            errors.append("volatility cap triggered (ATR>7%)")
        coin["score"] = total_score
        coin["atr"] = atr
        coin["bid"] = price * 0.999
        coin["ask"] = price * 1.001
        coin["layers"] = layers
        coin["ema_distance"] = ema_dist
        coin["adx_value"] = adx_val
        coin["trend_dir"] = trend_dir
        coin["errors"] = errors

        all_scored.append(coin)

        if best is None or abs(total_score) > abs(best_score):
            best = coin
            best_score = total_score
            best_layers = layers
            best_ema_distance = ema_dist
            best_adx = adx_val
            best_trend_dir = trend_dir
            best_errors = errors

    if btc_error:
        best_errors.append(f"intermarket: {btc_error}")

    all_scored_sorted = sorted(all_scored, key=lambda x: abs(x["score"]), reverse=True)
    coin_summary_list = []
    for c in all_scored_sorted:
        coin_summary_list.append(f"{c['symbol'].replace('USDT','')}: {c['score']:.2f}")
    coin_summary = " | ".join(coin_summary_list)

    if best is None or abs(best_score) < 1.49:
        best_sym = best["symbol"] if best else "none"
        layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
        err_str = ""
        if best_errors:
            err_str = " | Errors: " + "; ".join(best_errors)
        display_score = round(best_score, 2)
        reason = (f"No strong conviction. Best score: {display_score:+.2f}/3 for {best_sym}.\n"
                  f"Layers: {layer_str}{err_str}\n"
                  f"All coins: {coin_summary}")
        return {"action": "HOLD", "reasoning": reason}

    direction = "LONG" if best_score >= 0 else "SHORT"

    if best_trend_dir:
        if (direction == "LONG" and best_trend_dir == "down") or \
           (direction == "SHORT" and best_trend_dir == "up"):
            best_sym = best["symbol"]
            layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
            err_str = ""
            if best_errors:
                err_str = " | Errors: " + "; ".join(best_errors)
            display_score = round(best_score, 2)
            reason = (f"Signal {direction} rejected due to 4h trend filter ({best_trend_dir}). "
                      f"Best score: {display_score:+.2f}/3 for {best_sym}.\n"
                      f"Layers: {layer_str}{err_str}\n"
                      f"All coins: {coin_summary}")
            return {"action": "HOLD", "reasoning": reason}

    best_score += trend_strength_bonus(best_adx, best_score)
    momentum_bonus = momentum_alignment_score(best["symbol"], direction, best_layers)
    best_score += momentum_bonus

    if abs(best_score) < 1.49:
        best_sym = best["symbol"]
        layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
        err_str = ""
        if best_errors:
            err_str = " | Errors: " + "; ".join(best_errors)
        display_score = round(best_score, 2)
        reason = (f"No strong conviction after bonuses. Best score: {display_score:+.2f}/3 for {best_sym}.\n"
                  f"Layers: {layer_str}{err_str}\n"
                  f"All coins: {coin_summary}")
        return {"action": "HOLD", "reasoning": reason}

    entry = best["bid"] if direction == "LONG" else best["ask"]
    atr = best["atr"]
    min_stop_distance = max(1.5 * atr, entry * 0.01)
    stop = entry - min_stop_distance if direction == "LONG" else entry + min_stop_distance
    stop = round(stop, 6)
    risk_per_share = abs(entry - stop)

    # Fixed 1% risk per trade
    risk_percent = 0.01
    risk_amount = balance_usdt * risk_percent
    qty = round(risk_amount / risk_per_share, 6)

    mults = [0.4, 0.8, 1.2, 1.6, 2.0]
    tps = []
    for mult in mults:
        if direction == "LONG":
            tps.append(round(entry + mult * risk_per_share, 6))
        else:
            tps.append(round(entry - mult * risk_per_share, 6))

    conf, reason = call_groq_reasoning(best["symbol"], entry, atr, best_layers, best_errors)
    if conf < 5:
        layer_str = "; ".join([f"{k}={v:.2f}" for k,v in best_layers.items()])
        err_str = ""
        if best_errors:
            err_str = " | Errors: " + "; ".join(best_errors)
        display_score = round(best_score, 2)
        reason = (f"AI confidence too low ({conf}/10). Best score: {display_score:+.2f}/3 for {best['symbol']}.\n"
                  f"Layers: {layer_str}{err_str}\n"
                  f"All coins: {coin_summary}\n{reason}")
        return {"action": "HOLD", "reasoning": reason}

    conviction10 = round(best_score * 10 / 3)
    if conviction10 >= 0:
        conviction_str = f"+{conviction10}/10"
    else:
        conviction_str = f"{conviction10}/10"

    return {
        "action": direction,
        "symbol": best["symbol"],
        "quantity": qty,
        "limit_price": entry,
        "stop_loss": stop,
        "take_profits": tps,
        "confidence_score": conf,
        "reasoning": reason,
        "conviction_score": round(best_score, 2),
        "conviction10_str": conviction_str,
        "layers": best_layers,
        "errors": best_errors
    }

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("Telegram send failed:", e)

def main():
    try:
        initialize_trade_files()
        print("Checking open trades...")
        check_open_trades()

        daily_pnl = get_daily_pnl()
        if daily_pnl <= portfolio['daily_loss_limit']:
            msg = f"Daily loss limit reached (PnL: {daily_pnl:.2f} USD). No new trades today."
            send_telegram(msg)
            return

        balance = portfolio['balance_usdt']
        dec = generate_signal(balance)
        action = dec.get('action', 'HOLD')
        if action in ["LONG", "SHORT"]:
            log_signal(dec)
            add_open_trade(dec)
            portfolio['open_positions'] += 1
            save_portfolio(portfolio)

            raw_symbol = dec.get('symbol', '')
            symbol_display = raw_symbol.replace("USDT", "")
            direction_icon = "🟢" if action == "LONG" else "🔴"
            entry_price = dec.get('limit_price', 0)
            stop_price = dec.get('stop_loss', 0)
            tps = dec.get('take_profits', [])
            conviction_str = dec.get('conviction10_str', '0/10')

            tp_str = " / ".join([f"{tp:,.6f}" for tp in tps])

            if action == "LONG":
                header = f"📈${symbol_display} (LONG{direction_icon})"
            else:
                header = f"📉${symbol_display} (SHORT{direction_icon})"

            msg = (
                f"{header}\n"
                f"⛔Entry: {entry_price:,.6f}\n"
                f"🛑Stop: {stop_price:,.6f}\n"
                f"💰Targets: {tp_str}\n"
                f"✔️Confidence: {conviction_str}\n"
                f"Drop your entries below if you're taking this."
            )
            send_telegram(msg)
        else:
            msg = f"HOLD\n{dec.get('reasoning', 'No signal')}"
            send_telegram(msg)
    except Exception as e:
        err_msg = f"Bot crashed: {traceback.format_exc()}"
        print(err_msg)
        send_telegram(err_msg[:500])

if __name__ == "__main__":
    main()