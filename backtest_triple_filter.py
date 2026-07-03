#!/usr/bin/env python3
"""
Hard‑Filter Backtester – 4H, KuCoin, 1:10 RR, FIXED ATR STOP + 9‑EMA EXIT
Entry: EMA stack + ADX>25 + Volume>avg + Candle direction.
No scoring layers – picks highest ADX coin.
One trade at a time. $1000 account, 1% risk.
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
INITIAL_BALANCE = 1000.0          # $1,000 starting capital
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
# TECHNICAL INDICATORS (only what's needed for hard filters)
# ============================================================
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def atr(df, period=14):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

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
    return dx.ewm(alpha=1/period, adjust=False).mean().iloc[-1]

# ============================================================
# HARD FILTER ONLY (no scoring)
# ============================================================
def check_entry(df_4h, df_1h, df_d):
    """
    Returns (True, direction, price, atr_val) if all filters pass, else (False, None, ...)
    """
    if len(df_4h) < 50 or len(df_1h) < 10 or len(df_d) < 50:
        return False, None, None, None, None

    price = df_4h['Close'].iloc[-1]

    # 1. EMA Stack
    ema9 = ema(df_4h['Close'], 9).iloc[-1]
    ema20 = ema(df_4h['Close'], 20).iloc[-1]
    ema50 = ema(df_4h['Close'], 50).iloc[-1]
    ema200 = ema(df_4h['Close'], 200).iloc[-1]
    bullish_stack = (ema9 > ema20) and (ema20 > ema50) and (ema50 > ema200)
    bearish_stack = (ema9 < ema20) and (ema20 < ema50) and (ema50 < ema200)
    if not bullish_stack and not bearish_stack:
        return False, None, None, None, None

    # 2. ADX > 25
    adx_val = adx(df_4h)
    if adx_val is None or adx_val <= 25:
        return False, None, None, None, adx_val

    # 3. Volume > 20‑avg
    vol_last = df_4h['Volume'].iloc[-1]
    vol_avg = df_4h['Volume'].iloc[-21:-1].mean() if len(df_4h) >= 21 else vol_last
    if vol_avg > 0 and vol_last < vol_avg:
        return False, None, None, None, adx_val

    # Determine trend direction from EMA stack
    direction = "LONG" if bullish_stack else "SHORT"

    # 4. Candle direction
    last_candle = df_4h.iloc[-1]
    if direction == "LONG" and last_candle['Close'] <= last_candle['Open']:
        return False, None, None, None, adx_val
    if direction == "SHORT" and last_candle['Close'] >= last_candle['Open']:
        return False, None, None, None, adx_val

    atr_val = atr(df_4h)
    return True, direction, price, atr_val, adx_val

# ============================================================
# DATA FETCHING (unchanged)
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
# BACKTEST ENGINE (Fixed ATR stop + 9‑EMA close exit, 1:10 TP)
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

    print(f"Running HARD‑FILTER ONLY, 1:10 RR, $1000 account from {BACKTEST_START} to {end.date()}...")
    print(f"Timeline: {len(timeline)} 4H candles")

    for current_time in timeline:
        # 1. Check open trades (unchanged)
        closed_indices = []
        for idx, trade in enumerate(open_trades):
            sym = trade['symbol']
            if sym not in data:
                continue
            df_4h_sym = data[sym]['4h']
            if current_time not in df_4h_sym.index:
                continue
            bar = df_4h_sym.loc[current_time]
            closes = df_4h_sym['Close'].loc[:current_time]
            ema9_series = ema(closes, 9)
            ema9_val = ema9_series.iloc[-1]
            high, low, close = bar['High'], bar['Low'], bar['Close']
            entry, tp, hard_stop = trade['entry'], trade['tp'], trade['hard_stop']
            direction = trade['direction']
            qty = trade['quantity']
            exit_reason = None; exit_price = None

            # TP
            if direction == "LONG":
                if high >= tp:
                    exit_reason = "TP"; exit_price = tp
            else:
                if low <= tp:
                    exit_reason = "TP"; exit_price = tp

            # Hard stop
            if exit_reason is None:
                if direction == "LONG":
                    if low <= hard_stop:
                        exit_reason = "HARD SL"; exit_price = hard_stop
                else:
                    if high >= hard_stop:
                        exit_reason = "HARD SL"; exit_price = hard_stop

            # EMA close exit
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

        # 2. Generate new signal if no open trade (using only hard filters)
        if len(open_trades) == 0:
            candidates = []
            for sym, sym_data in data.items():
                df_4h = sym_data['4h'].loc[:current_time]
                df_1h = sym_data['1h'].loc[:current_time]
                df_d = sym_data['1d'].loc[:current_time]
                if len(df_4h) < 50:
                    continue
                ok, direction, price, atr_val, adx_val = check_entry(df_4h, df_1h, df_d)
                if not ok:
                    continue

                # Fixed ATR stop (2‑6% clamp)
                min_stop_pct = 0.02
                max_stop_pct = 0.06
                raw_stop = atr_val * 2.5 if (atr_val is not None and not math.isnan(atr_val)) else price * 0.02
                stop_distance = np.clip(raw_stop, price * min_stop_pct, price * max_stop_pct)

                if direction == "LONG":
                    hard_stop = price - stop_distance
                else:
                    hard_stop = price + stop_distance

                risk = abs(price - hard_stop)
                if risk <= 0:
                    continue

                # 10R target
                if direction == "LONG":
                    tp = price + 10 * risk
                else:
                    tp = price - 10 * risk

                qty = round((balance * RISK_PER_TRADE) / risk, 8)

                candidates.append({
                    'symbol': sym, 'direction': direction, 'entry': price,
                    'hard_stop': hard_stop, 'tp': tp, 'quantity': qty,
                    'adx': adx_val
                })
            if candidates:
                # Pick the coin with the highest ADX (strongest trend)
                best = max(candidates, key=lambda x: x['adx'])
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
        f"BACKTEST RESULTS (Hard Filters Only, 1:10 RR)\n"
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