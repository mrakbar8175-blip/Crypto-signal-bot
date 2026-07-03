#!/usr/bin/env python3
"""
Triple‑Filter Backtester – 4H timeframe, KuCoin data, 1:2 RR partials
Uses: EMA stack, ADX > 25, volume > avg, candle direction
Partial exits 30/10/10/10/40, trailing stop (BE after TP1, etc.)
Usage: python backtest_triple_filter.py
"""

import ccxt
import pandas as pd
import numpy as np
import os, sys, time, math
from datetime import datetime, timedelta

# ============================================================
# CONFIGURATION
# ============================================================
BACKTEST_START = "2025-01-01"
INITIAL_BALANCE = 1000.0       # Change to any starting amount
RISK_PER_TRADE = 0.01          # 1% risk per trade
MAX_RISKY_TRADES = 5
DATA_FOLDER = "kucoin_data_backtest"

# Top‑100 coins (we'll fetch only those that have data)
CRYPTO_PAIRS = [
    "BTC-USDT","ETH-USDT","BNB-USDT","SOL-USDT","XRP-USDT",
    "ADA-USDT","DOGE-USDT","DOT-USDT","MATIC-USDT","LINK-USDT",
    "UNI-USDT","AVAX-USDT","LTC-USDT","FIL-USDT","TRX-USDT",
    "ATOM-USDT","XLM-USDT","ETC-USDT","BCH-USDT","NEAR-USDT",
    "VET-USDT","ICP-USDT","HBAR-USDT","APT-USDT","ARB-USDT",
    "OP-USDT","GRT-USDT","THETA-USDT","ALGO-USDT","FTM-USDT",
    "EGLD-USDT","IMX-USDT","SAND-USDT","AXS-USDT","MANA-USDT",
    "AAVE-USDT","MKR-USDT","SNX-USDT","CRV-USDT","COMP-USDT",
    "ZEC-USDT","BAT-USDT","ENJ-USDT","CHZ-USDT","HOT-USDT",
    "KSM-USDT","DASH-USDT","CELO-USDT","QTUM-USDT","IOST-USDT",
    # Add more pairs to reach 100 – here we list a subset, but the script will fetch top 100 from KuCoin
    # For simplicity, we'll use a static list of 100 pairs (you can expand)
]
# If you want real top‑100 from KuCoin, use exchange.fetch_markets() – but static list works for backtest.

# ============================================================
# TECHNICAL INDICATORS (same as live bot)
# ============================================================
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def atr(df, period=14):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

def rsi(df, period=14):
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs)).iloc[-1] if not rs.isna().iloc[-1] else None

def macd(df):
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    macd_line = exp1 - exp2
    signal = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal
    return (macd_line.iloc[-1], signal.iloc[-1], histogram.iloc[-1],
            histogram.iloc[-2] if len(histogram) > 1 else 0)

def adx(df, period=14):
    h, l, c = df['High'], df['Low'], df['Close']
    dm_plus = h.diff()
    dm_minus = -l.diff()
    dm_plus[dm_plus < 0] = 0
    dm_minus[dm_minus < 0] = 0
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_val = tr.ewm(alpha=1/period, adjust=False).mean()
    di_plus = 100 * (dm_plus.ewm(alpha=1/period, adjust=False).mean() / atr_val)
    di_minus = 100 * (dm_minus.ewm(alpha=1/period, adjust=False).mean() / atr_val)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
    return dx.ewm(alpha=1/period, adjust=False).mean().iloc[-1], di_plus.iloc[-1], di_minus.iloc[-1]

def support_resistance_levels(df, lookback=20):
    recent = df.tail(lookback)
    return recent['High'].max(), recent['Low'].min()

# ============================================================
# SCORING (Triple Filter + 11 layers)
# ============================================================
def score_pair(df_4h, df_1h, df_d, btc_df_4h=None):
    """
    Returns (total_score, direction, price, atr_val, swing_level, layers)
    or (0, None, ...) if any filter fails.
    """
    layers = {}
    if len(df_4h) < 50 or len(df_1h) < 10 or len(df_d) < 50:
        return 0, None, None, None, None, {}

    price = df_4h['Close'].iloc[-1]

    # ---- FILTER 1: EMA Stack Alignment ----
    ema9 = ema(df_4h['Close'], 9).iloc[-1]
    ema20 = ema(df_4h['Close'], 20).iloc[-1]
    ema50 = ema(df_4h['Close'], 50).iloc[-1]
    ema200 = ema(df_4h['Close'], 200).iloc[-1]

    bullish_stack = (ema9 > ema20) and (ema20 > ema50) and (ema50 > ema200)
    bearish_stack = (ema9 < ema20) and (ema20 < ema50) and (ema50 < ema200)
    if not bullish_stack and not bearish_stack:
        return 0, None, None, None, None, {}

    # ---- FILTER 2: ADX > 25 ----
    adx_val, di_plus, di_minus = adx(df_4h)
    if adx_val is None or adx_val <= 25:
        return 0, None, None, None, None, {}

    # ---- FILTER 3: Volume > 20‑avg ----
    vol_last = df_4h['Volume'].iloc[-1]
    vol_avg = df_4h['Volume'].iloc[-21:-1].mean() if len(df_4h) >= 21 else vol_last
    if vol_avg > 0 and vol_last < vol_avg:
        return 0, None, None, None, None, {}

    # Determine trend direction
    ema50_d = ema(df_d['Close'], 50); ema200_d = ema(df_d['Close'], 200)
    trend_daily = 0
    if price > ema50_d.iloc[-1] and ema50_d.iloc[-1] > ema200_d.iloc[-1]:
        trend_daily = 1
    elif price < ema50_d.iloc[-1] and ema50_d.iloc[-1] < ema200_d.iloc[-1]:
        trend_daily = -1
    if trend_daily == 0:
        if bullish_stack: trend_daily = 1
        elif bearish_stack: trend_daily = -1
        else: return 0, None, None, None, None, {}

    direction = "LONG" if trend_daily == 1 else "SHORT"

    # ---- FILTER 4: Candle direction ----
    last_candle = df_4h.iloc[-1]
    if direction == "LONG" and last_candle['Close'] <= last_candle['Open']:
        return 0, None, None, None, None, {}
    if direction == "SHORT" and last_candle['Close'] >= last_candle['Open']:
        return 0, None, None, None, None, {}

    # ---- Remaining indicators for scoring ----
    rsi_val = rsi(df_4h)
    macd_line, macd_signal, macd_hist, macd_hist_prev = macd(df_4h)
    atr_val = atr(df_4h)
    res, sup = support_resistance_levels(df_4h, 20)

    # BTC context (optional)
    market_aligned = False
    if btc_df_4h is not None and len(btc_df_4h) >= 50:
        btc_ema50 = ema(btc_df_4h['Close'], 50)
        btc_trend_up = btc_df_4h['Close'].iloc[-1] > btc_ema50.iloc[-1]
        if trend_daily == 1 and btc_trend_up: market_aligned = True
        elif trend_daily == -1 and not btc_trend_up: market_aligned = True
    else: layers["Market"] = (0, 0.5, "FAIL")

    # 1H momentum
    df_1h_last = df_1h.iloc[-1]
    df_1h_prev = df_1h.iloc[-2]
    candle_range_1h = df_1h_last['High'] - df_1h_last['Low']
    bullish_momentum = (df_1h_last['Close'] - df_1h_last['Open']) / candle_range_1h if candle_range_1h > 0 else 0

    def bool_score(cond): return 1 if cond else 0

    # 11 layers
    if direction == "LONG": ema_align = price > ema50 and ema50 > ema200
    else: ema_align = price < ema50 and ema50 < ema200
    layers["EMA Align"] = (bool_score(ema_align) * 1.5, 1.5, "OK")
    adx_trending = adx_val > 20
    adx_dir = (di_plus > di_minus) if direction == "LONG" else (di_minus > di_plus)
    layers["ADX"] = (bool_score(adx_trending and adx_dir) * 1.0, 1.0, "OK")
    if rsi_val is not None: layers["RSI"] = (bool_score((direction=="LONG" and rsi_val>50) or (direction=="SHORT" and rsi_val<50)) * 1.5, 1.5, "OK")
    else: layers["RSI"] = (0, 1.5, "FAIL")
    macd_expanding = (direction=="LONG" and macd_hist>0 and macd_hist>macd_hist_prev) or (direction=="SHORT" and macd_hist<0 and macd_hist<macd_hist_prev)
    layers["MACD"] = (bool_score(macd_expanding) * 1.0, 1.0, "OK")
    if atr_val and atr_val>0:
        if direction=="LONG": sr_score = bool_score((price-sup) < atr_val*0.5)
        else: sr_score = bool_score((res-price) < atr_val*0.5)
        layers["S/R"] = (sr_score*1.0, 1.0, "OK")
    else: layers["S/R"] = (0, 1.0, "FAIL")
    layers["Volume"] = (bool_score(True) * 0.5, 0.5, "OK")  # already filtered
    if "Market" not in layers: layers["Market"] = (bool_score(market_aligned)*0.5, 0.5, "OK")
    candle_ok = (bullish_momentum > 0.5) if direction=="LONG" else (bullish_momentum < -0.5)
    layers["Candle Mom"] = (bool_score(candle_ok)*2.0, 2.0, "OK")
    rsi_1h_val = rsi(df_1h, 14)
    if rsi_1h_val is not None:
        rsi_1h_ok = (rsi_1h_val < 63) if direction=="LONG" else (rsi_1h_val > 37)
        layers["RSI 1h"] = (bool_score(rsi_1h_ok)*1.5, 1.5, "OK")
    else: layers["RSI 1h"] = (0, 1.5, "FAIL")
    if atr_val and price>0: layers["ATR"] = (bool_score(atr_val > price*0.005)*1.0, 1.0, "OK")
    else: layers["ATR"] = (0, 1.0, "FAIL")
    if direction=="LONG": micro_ok = df_1h_last['Close'] > df_1h_last['Open'] and df_1h_prev['Close'] > df_1h_prev['Open']
    else: micro_ok = df_1h_last['Close'] < df_1h_last['Open'] and df_1h_prev['Close'] < df_1h_prev['Open']
    layers["Micro Trend"] = (bool_score(micro_ok)*2.0, 2.0, "OK")
    total = sum(score for score,_,_ in layers.values() if isinstance(score,(int,float)))
    return total, direction, price, atr_val, (sup if direction=="LONG" else res), layers

# ============================================================
# DATA FETCHING
# ============================================================
def fetch_kucoin_klines(symbol, timeframe, start_date, end_date):
    exchange = ccxt.kucoin({'enableRateLimit': True})
    since = exchange.parse8601(start_date + "T00:00:00Z")
    all_candles = []
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1500)
            if not candles:
                break
            all_candles += candles
            since = candles[-1][0] + 1
            if len(candles) < 1500:
                break
            time.sleep(0.2)
        except Exception as e:
            print(f"Error: {e}")
            break
    if not all_candles:
        return pd.DataFrame()
    df = pd.DataFrame(all_candles, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df

def get_data_for_pair(ccxt_symbol, interval):
    file_name = os.path.join(DATA_FOLDER, f"{ccxt_symbol.replace('/', '_')}_{interval}.parquet")
    if os.path.exists(file_name):
        df = pd.read_parquet(file_name)
        # Filter to backtest period
        df = df[df.index >= BACKTEST_START]
    else:
        df = fetch_kucoin_klines(ccxt_symbol, interval, BACKTEST_START, datetime.now().strftime("%Y-%m-%d"))
        if not df.empty:
            os.makedirs(DATA_FOLDER, exist_ok=True)
            df.to_parquet(file_name)
    return df

# ============================================================
# BACKTEST ENGINE
# ============================================================
def run_backtest():
    print("Loading data...")
    data = {}
    # Fetch data for all pairs and store
    for pair in CRYPTO_PAIRS[:50]:   # limit to 50 to keep runtime reasonable; can increase
        ccxt_symbol = pair.replace("-USDT", "/USDT")
        df_4h = get_data_for_pair(ccxt_symbol, '4h')
        df_1h = get_data_for_pair(ccxt_symbol, '1h')
        if df_4h.empty or df_1h.empty:
            continue
        # Resample daily from 4h
        df_d = df_4h.resample('1d').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
        yahoo_sym = pair.replace("-USDT", "-USD")
        data[yahoo_sym] = {'4h': df_4h, '1h': df_1h, '1d': df_d}

    # BTC data for context
    btc_4h = data.get("BTC-USD", {}).get('4h')
    if btc_4h is None:
        print("BTC data missing – cannot run.")
        return

    # Create a unified timeline from BTC 4h
    start = pd.Timestamp(BACKTEST_START)
    end = pd.Timestamp.now()
    timeline = btc_4h.index[(btc_4h.index >= start) & (btc_4h.index <= end)]

    balance = INITIAL_BALANCE
    open_trades = []
    trade_log = []
    equity = []

    print(f"Running backtest from {BACKTEST_START} to {end.date()}...")
    print(f"Timeline: {len(timeline)} 4H candles")

    for current_time in timeline:
        # 1. Check open trades
        closed_indices = []
        for idx, trade in enumerate(open_trades):
            sym = trade['symbol']
            if sym not in data:
                continue
            df_4h_sym = data[sym]['4h']
            if current_time not in df_4h_sym.index:
                continue
            bar = df_4h_sym.loc[current_time]
            high, low = bar['High'], bar['Low']
            entry, stop, tps = trade['entry'], trade['stop'], trade['tps']
            direction = trade['direction']
            remaining_qty = trade['quantity']
            fractions = [0.30, 0.10, 0.10, 0.10, 0.40]

            # Check TPs from highest to lowest index
            new_tp_idx = None
            if direction == "LONG":
                for i in range(len(tps)-1, -1, -1):
                    if high >= tps[i] and i > trade.get('highest_tp', -1):
                        new_tp_idx = i
                        break
            else:
                for i in range(len(tps)-1, -1, -1):
                    if low <= tps[i] and i > trade.get('highest_tp', -1):
                        new_tp_idx = i
                        break

            if new_tp_idx is not None:
                for i in range(trade.get('highest_tp', -1)+1, new_tp_idx+1):
                    if remaining_qty <= 0:
                        break
                    frac = fractions[i]
                    exit_qty = trade['original_qty'] * frac
                    if exit_qty > remaining_qty:
                        exit_qty = remaining_qty
                    if exit_qty > 0:
                        exit_price = tps[i]
                        pnl = (exit_price - entry) * exit_qty if direction == "LONG" else (entry - exit_price) * exit_qty
                        trade_log.append({
                            'timestamp': current_time, 'symbol': sym, 'action': direction,
                            'hit_level': f"TP{i+1}", 'exit_price': exit_price,
                            'quantity': exit_qty, 'pnl': round(pnl, 4)
                        })
                        balance += pnl
                        remaining_qty -= exit_qty
                        trade['highest_tp'] = i
                        if i == 0:
                            trade['breakeven'] = True
                if remaining_qty <= 0:
                    closed_indices.append(idx)
                    continue

            # Update stop based on trailing
            current_stop = entry if trade.get('breakeven', False) else stop
            if trade.get('highest_tp', -1) >= 0:
                if trade['highest_tp'] == 0:
                    current_stop = entry
                elif trade['highest_tp'] == 1:
                    current_stop = tps[0]
                elif trade['highest_tp'] >= 2:
                    current_stop = tps[1]  # etc.

            # Check stop loss
            sl_hit = (low <= current_stop) if direction == "LONG" else (high >= current_stop)
            if sl_hit and remaining_qty > 0:
                exit_price = current_stop
                pnl = (exit_price - entry) * remaining_qty if direction == "LONG" else (entry - exit_price) * remaining_qty
                trade_log.append({
                    'timestamp': current_time, 'symbol': sym, 'action': direction,
                    'hit_level': "SL", 'exit_price': exit_price,
                    'quantity': remaining_qty, 'pnl': round(pnl, 4)
                })
                balance += pnl
                closed_indices.append(idx)

        for idx in sorted(closed_indices, reverse=True):
            open_trades.pop(idx)

        # 2. Generate new signals (up to MAX_RISKY_TRADES)
        risky_count = len(open_trades)
        if risky_count < MAX_RISKY_TRADES:
            candidates = []
            open_symbols = {t['symbol'] for t in open_trades}
            for sym, sym_data in data.items():
                if sym in open_symbols:
                    continue
                df_4h = sym_data['4h'].loc[:current_time]
                df_1h = sym_data['1h'].loc[:current_time]
                df_d = sym_data['1d'].loc[:current_time]
                if len(df_4h) < 50:
                    continue
                score, direction, price, atr_val, swing_level, layers = score_pair(
                    df_4h, df_1h, df_d, btc_4h.loc[:current_time] if btc_4h is not None else None
                )
                if direction is None or score < 6.0:
                    continue
                # Compute stop & TPs (same as live)
                min_stop_pct = 0.02; max_stop_pct = 0.06
                raw_stop = (atr_val * 2.5) if (atr_val is not None and not math.isnan(atr_val)) else price * 0.02
                stop_distance = np.clip(raw_stop, price*min_stop_pct, price*max_stop_pct)
                if direction == "LONG":
                    stop = price - stop_distance
                    if swing_level and swing_level > price - stop_distance*1.2:
                        stop = min(stop, swing_level - 0.05*(atr_val if atr_val else price*0.01))
                else:
                    stop = price + stop_distance
                    if swing_level and swing_level < price + stop_distance*1.2:
                        stop = max(stop, swing_level + 0.05*(atr_val if atr_val else price*0.01))
                risk = abs(price - stop)
                tp_multipliers = [0.4, 0.8, 1.2, 1.6, 2.0]
                tps = [round(price + m*risk, 6) if direction=="LONG" else round(price - m*risk, 6) for m in tp_multipliers]
                qty = round((balance * RISK_PER_TRADE) / risk, 8)
                candidates.append({
                    'symbol': sym, 'direction': direction, 'entry': price,
                    'stop': stop, 'tps': tps, 'quantity': qty, 'original_qty': qty,
                    'highest_tp': -1, 'breakeven': False, 'score': score
                })
            # Take the highest scored candidate
            if candidates:
                best = max(candidates, key=lambda x: x['score'])
                open_trades.append(best)

        # Record equity
        equity.append((current_time, balance))

    # Close any remaining trades at last price
    for trade in open_trades:
        sym = trade['symbol']
        if sym in data:
            last_price = data[sym]['4h']['Close'].iloc[-1]
            entry = trade['entry']
            remaining_qty = trade['quantity']
            direction = trade['direction']
            pnl = (last_price - entry) * remaining_qty if direction == "LONG" else (entry - last_price) * remaining_qty
            trade_log.append({
                'timestamp': data[sym]['4h'].index[-1], 'symbol': sym, 'action': direction,
                'hit_level': 'MARKET CLOSE', 'exit_price': last_price,
                'quantity': remaining_qty, 'pnl': round(pnl, 4)
            })
            balance += pnl

    # Performance metrics
    if not trade_log:
        print("No trades were generated.")
        return
    trades_df = pd.DataFrame(trade_log)
    # Group by (timestamp, symbol) to get trade-level P&L
    groups = trades_df.groupby(['timestamp', 'symbol'])
    full_trades = []
    for (ts, sym), grp in groups:
        total_pnl = grp['pnl'].sum()
        full_trades.append({'entry_time': ts, 'symbol': sym, 'total_pnl': total_pnl, 'action': grp['action'].iloc[0]})
    full_df = pd.DataFrame(full_trades).sort_values('entry_time')
    full_df['is_win'] = full_df['total_pnl'] > 0
    wins = full_df[full_df['is_win']]
    losses = full_df[~full_df['is_win']]
    total_trades = len(full_df)
    total_pnl = full_df['total_pnl'].sum()
    winrate = len(wins) / max(total_trades, 1) * 100
    profit_factor = wins['total_pnl'].sum() / abs(losses['total_pnl'].sum()) if len(losses) > 0 else float('inf')
    final_balance = INITIAL_BALANCE + total_pnl

    # Drawdown
    eq_df = pd.DataFrame(equity, columns=['time', 'balance'])
    eq_df['peak'] = eq_df['balance'].cummax()
    eq_df['dd'] = (eq_df['peak'] - eq_df['balance']) / eq_df['peak']
    max_dd = eq_df['dd'].max() * 100

    # Print summary
    summary = (
        f"\n{'='*50}\n"
        f"BACKTEST RESULTS (Triple Filter + Partials)\n"
        f"{'='*50}\n"
        f"Period: {BACKTEST_START} → {datetime.now().strftime('%Y-%m-%d')}\n"
        f"Initial Balance: ${INITIAL_BALANCE:.2f}\n"
        f"Final Balance: ${final_balance:.2f}\n"
        f"Total Trades: {total_trades}\n"
        f"Winrate: {winrate:.1f}% ({len(wins)}W / {len(losses)}L)\n"
        f"Total P&L: ${total_pnl:.2f}\n"
        f"Profit Factor: {profit_factor:.2f}\n"
        f"Max Drawdown: {max_dd:.2f}%\n"
        f"{'='*50}"
    )
    print(summary)
    with open("backtest_summary.txt", "w") as f:
        f.write(summary)
    full_df.to_csv("backtest_trades.csv", index=False)
    print("\nResults saved to backtest_summary.txt and backtest_trades.csv")

if __name__ == "__main__":
    run_backtest()