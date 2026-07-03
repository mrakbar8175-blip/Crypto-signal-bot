#!/usr/bin/env python3
"""
Triple‑Filter Backtester – 4H, KuCoin, 1:10 RR, FIXED CANDLE STOP + 9‑EMA CLOSE EXIT
Entry: triple filter + 11‑layer scoring.
Exit: 10R TP (risk = distance to previous candle's low/high), or that fixed stop hit,
      or candle closes beyond 9‑EMA against trade.
One trade at a time, highest‑scored coin.
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
INITIAL_BALANCE = 1000.0          # $1000 account
RISK_PER_TRADE = 0.01             # 1% risk per trade
MAX_RISKY_TRADES = 1              # one trade at a time
DATA_FOLDER = "kucoin_data_backtest"

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
    "KSM-USDT","DASH-USDT","CELO-USDT","QTUM-USDT","IOST-USDT"
]

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
# SCORING (Triple Filter + 11 layers) – exactly as live bot
# ============================================================
def score_pair(df_4h, df_1h, df_d, btc_df_4h=None):
    layers = {}
    if len(df_4h) < 50 or len(df_1h) < 10 or len(df_d) < 50:
        return 0, None, None, None, None, {}

    price = df_4h['Close'].iloc[-1]

    # FILTER 1: EMA Stack
    ema9 = ema(df_4h['Close'], 9).iloc[-1]
    ema20 = ema(df_4h['Close'], 20).iloc[-1]
    ema50 = ema(df_4h['Close'], 50).iloc[-1]
    ema200 = ema(df_4h['Close'], 200).iloc[-1]
    bullish_stack = (ema9 > ema20) and (ema20 > ema50) and (ema50 > ema200)
    bearish_stack = (ema9 < ema20) and (ema20 < ema50) and (ema50 < ema200)
    if not bullish_stack and not bearish_stack:
        return 0, None, None, None, None, {}

    # FILTER 2: ADX > 25
    adx_val, di_plus, di_minus = adx(df_4h)
    if adx_val is None or adx_val <= 25:
        return 0, None, None, None, None, {}

    # FILTER 3: Volume > 20‑avg
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

    # FILTER 4: Candle direction
    last_candle = df_4h.iloc[-1]
    if direction == "LONG" and last_candle['Close'] <= last_candle['Open']:
        return 0, None, None, None, None, {}
    if direction == "SHORT" and last_candle['Close'] >= last_candle['Open']:
        return 0, None, None, None, None, {}

    # ---- Remaining indicators ----
    rsi_val = rsi(df_4h)
    macd_line, macd_signal, macd_hist, macd_hist_prev = macd(df_4h)
    atr_val = atr(df_4h)
    res, sup = support_resistance_levels(df_4h, 20)

    # BTC context
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

    # 11 layers (identical weights)
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
    layers["Volume"] = (bool_score(True) * 0.5, 0.5, "OK")
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
        df = df[df.index >= BACKTEST_START]
    else:
        df = fetch_kucoin_klines(ccxt_symbol, interval, BACKTEST_START, datetime.now().strftime("%Y-%m-%d"))
        if not df.empty:
            os.makedirs(DATA_FOLDER, exist_ok=True)
            df.to_parquet(file_name)
    return df

# ============================================================
# BACKTEST ENGINE (Candle Stop + 9‑EMA close exit, 1:10 TP)
# ============================================================
def run_backtest():
    print("Loading data...")
    data = {}
    for pair in CRYPTO_PAIRS[:50]:
        ccxt_symbol = pair.replace("-USDT", "/USDT")
        df_4h = get_data_for_pair(ccxt_symbol, '4h')
        df_1h = get_data_for_pair(ccxt_symbol, '1h')
        if df_4h.empty or df_1h.empty:
            continue
        df_d = df_4h.resample('1d').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()
        yahoo_sym = pair.replace("-USDT", "-USD")
        data[yahoo_sym] = {'4h': df_4h, '1h': df_1h, '1d': df_d}

    btc_4h = data.get("BTC-USD", {}).get('4h')
    if btc_4h is None:
        print("BTC data missing – cannot run.")
        return

    start = pd.Timestamp(BACKTEST_START)
    end = pd.Timestamp.now()
    timeline = btc_4h.index[(btc_4h.index >= start) & (btc_4h.index <= end)]

    balance = INITIAL_BALANCE
    open_trades = []
    trade_log = []
    equity = []

    print(f"Running Candle Stop + 9‑EMA Close Exit, 1:10 RR from {BACKTEST_START} to {end.date()}...")
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

            # Compute 9‑EMA for close condition
            closes = df_4h_sym['Close'].loc[:current_time]
            ema9_series = ema(closes, 9)
            ema9_val = ema9_series.iloc[-1]

            high, low, close = bar['High'], bar['Low'], bar['Close']
            entry, tp, candle_stop = trade['entry'], trade['tp'], trade['candle_stop']
            direction = trade['direction']
            qty = trade['quantity']

            exit_reason = None
            exit_price = None

            # 1) TP hit (intra‑bar)
            if direction == "LONG":
                if high >= tp:
                    exit_reason = "TP"; exit_price = tp
            else:
                if low <= tp:
                    exit_reason = "TP"; exit_price = tp

            # 2) Fixed candle stop hit (intra‑bar)
            if exit_reason is None:
                if direction == "LONG":
                    if low <= candle_stop:
                        exit_reason = "CANDLE SL"; exit_price = candle_stop
                else:
                    if high >= candle_stop:
                        exit_reason = "CANDLE SL"; exit_price = candle_stop

            # 3) 9‑EMA close condition (after bar)
            if exit_reason is None:
                if direction == "LONG" and close < ema9_val:
                    exit_reason = "EMA EXIT"; exit_price = close
                elif direction == "SHORT" and close > ema9_val:
                    exit_reason = "EMA EXIT"; exit_price = close

            if exit_reason is not None:
                pnl = (exit_price - entry) * qty if direction == "LONG" else (entry - exit_price) * qty
                trade_log.append({
                    'timestamp': current_time, 'symbol': sym, 'action': direction,
                    'hit_level': exit_reason, 'exit_price': exit_price,
                    'quantity': qty, 'pnl': round(pnl, 4)
                })
                balance += pnl
                closed_indices.append(idx)

        for idx in sorted(closed_indices, reverse=True):
            open_trades.pop(idx)

        # 2. Generate new signal if no open trade
        if len(open_trades) == 0:
            candidates = []
            for sym, sym_data in data.items():
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

                # Fixed stop = previous candle's low (long) or high (short)
                prev_candle = df_4h.iloc[-2]   # previous candle (the one before entry)
                if direction == "LONG":
                    candle_stop = prev_candle['Low']
                    # Ensure stop is below entry; if not, skip trade
                    if candle_stop >= price:
                        continue
                else:
                    candle_stop = prev_candle['High']
                    if candle_stop <= price:
                        continue

                # Minimum stop distance (0.2% of price) to avoid absurdly tiny risk
                min_risk = 0.002 * price
                if abs(price - candle_stop) < min_risk:
                    continue

                risk = abs(price - candle_stop)

                # 10R target based on this risk
                if direction == "LONG":
                    tp = price + 10 * risk
                else:
                    tp = price - 10 * risk

                qty = round((balance * RISK_PER_TRADE) / risk, 8)

                candidates.append({
                    'symbol': sym, 'direction': direction, 'entry': price,
                    'candle_stop': candle_stop, 'tp': tp, 'quantity': qty, 'score': score
                })
            if candidates:
                best = max(candidates, key=lambda x: x['score'])
                open_trades.append(best)

        equity.append((current_time, balance))

    # Close remaining trades at last price
    for trade in open_trades:
        sym = trade['symbol']
        if sym in data:
            last_price = data[sym]['4h']['Close'].iloc[-1]
            entry = trade['entry']; qty = trade['quantity']; direction = trade['direction']
            pnl = (last_price - entry) * qty if direction == "LONG" else (entry - last_price) * qty
            trade_log.append({
                'timestamp': data[sym]['4h'].index[-1], 'symbol': sym, 'action': direction,
                'hit_level': 'MARKET CLOSE', 'exit_price': last_price,
                'quantity': qty, 'pnl': round(pnl, 4)
            })
            balance += pnl

    # Performance metrics
    if not trade_log:
        print("No trades were generated.")
        return
    trades_df = pd.DataFrame(trade_log)
    groups = trades_df.groupby(['timestamp', 'symbol'])
    full_trades = []
    for (ts, sym), grp in groups:
        total_pnl = grp['pnl'].sum()
        full_trades.append({'entry_time': ts, 'symbol': sym, 'total_pnl': total_pnl, 'action': grp['action'].iloc[0]})
    full_df = pd.DataFrame(full_trades).sort_values('entry_time')
    full_df['is_win'] = full_df['total_pnl'] > 0
    wins = full_df[full_df['is_win']]; losses = full_df[~full_df['is_win']]
    total_trades = len(full_df)
    total_pnl = full_df['total_pnl'].sum()
    winrate = len(wins) / max(total_trades, 1) * 100
    profit_factor = wins['total_pnl'].sum() / abs(losses['total_pnl'].sum()) if len(losses) > 0 else float('inf')
    final_balance = INITIAL_BALANCE + total_pnl

    eq_df = pd.DataFrame(equity, columns=['time', 'balance'])
    eq_df['peak'] = eq_df['balance'].cummax()
    eq_df['dd'] = (eq_df['peak'] - eq_df['balance']) / eq_df['peak']
    max_dd = eq_df['dd'].max() * 100

    summary = (
        f"\n{'='*50}\n"
        f"BACKTEST RESULTS (Candle Stop + 9‑EMA Close Exit, 1:10 RR)\n"
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