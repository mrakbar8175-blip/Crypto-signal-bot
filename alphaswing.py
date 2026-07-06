#!/usr/bin/env python3
"""
AlphaSwing v4 – Quant Signal Generator + Auto-Journal (4H)
Generates signals, monitors trades, and automatically journals performance.
Reports stats every 10 trades.

Usage:
    python alphaswing.py              # Generate signals
    python alphaswing.py --monitor    # Monitor open trades
    python alphaswing.py --loop       # Both, every 4 hours
    python alphaswing.py --report     # Show performance report now
"""

import os, json, time, sys, atexit
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ======================== CONFIGURATION ========================
CONFIG = {
    "trading": {
        "max_signals": 3,
        "risk_per_trade_pct": 1.0,
        "min_score_to_enter": 1.5,
        "atr_stop_multiplier": 2.0,
        "tp_multipliers": [0.5, 1.0, 1.5, 2.0, 3.0],
        "fractions": [0.40, 0.20, 0.15, 0.15, 0.10],
        "trailing_atr_multiplier": 1.5,
    },
    "universe": {
        "limit": 50,
        "blacklist": [
            "USDT","USDC","DAI","BUSD","TUSD","USDP","FDUSD",
            "LEO","WBT","USD1","USDS","USDE","PYUSD","STETH"
        ]
    },
    "files": {
        "portfolio_file": "portfolio.json",
        "signal_log": "signal_log.csv",
        "open_trades_file": "open_trades.json",
        "trade_results_file": "trade_results.csv",
        "perf_counter_file": "perf_counter.txt",
    },
    "loop_interval_hours": 4,
    "report_every_n_trades": 10,
}

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
LOCK_FILE = "bot.lock"

# ======================== LOCK ========================
def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            if time.time() - os.path.getmtime(LOCK_FILE) < 120:
                print("Another instance running. Exiting."); sys.exit(0)
        except: pass
    with open(LOCK_FILE, 'w') as f: f.write(str(time.time()))

def release_lock():
    try: os.remove(LOCK_FILE)
    except: pass

atexit.register(release_lock)

# ======================== AUTO FILE INITIALIZATION ========================
def initialize_files():
    """Automatically create all required files if they don't exist."""
    print("[*] Initializing data files...")
    
    # Create portfolio.json
    if not os.path.exists(CONFIG["files"]["portfolio_file"]):
        with open(CONFIG["files"]["portfolio_file"], 'w') as f:
            json.dump({"balance": 1000.0}, f, indent=2)
        print(f"  ✓ Created {CONFIG['files']['portfolio_file']}")
    
    # Create open_trades.json
    if not os.path.exists(CONFIG["files"]["open_trades_file"]):
        with open(CONFIG["files"]["open_trades_file"], 'w') as f:
            json.dump([], f)
        print(f"  ✓ Created {CONFIG['files']['open_trades_file']}")
    
    # Create signal_log.csv with headers
    if not os.path.exists(CONFIG["files"]["signal_log"]):
        cols = ["timestamp", "symbol", "direction", "entry", "stop", 
                "tp1", "tp2", "tp3", "score", "mom_z", "clv", "qty", "notional"]
        pd.DataFrame(columns=cols).to_csv(CONFIG["files"]["signal_log"], index=False)
        print(f"  ✓ Created {CONFIG['files']['signal_log']}")
    
    # Create trade_results.csv with headers
    if not os.path.exists(CONFIG["files"]["trade_results_file"]):
        cols = ["open_time", "close_time", "symbol", "direction", "entry", "stop",
                "tp1", "tp2", "tp3", "tp4", "tp5", "exit_price", "qty", "pnl_pct",
                "pnl_dollars", "r_multiple", "hit_level", "score", "mom_z", "clv"]
        pd.DataFrame(columns=cols).to_csv(CONFIG["files"]["trade_results_file"], index=False)
        print(f"  ✓ Created {CONFIG['files']['trade_results_file']}")
    
    # Create perf_counter.txt
    if not os.path.exists(CONFIG["files"]["perf_counter_file"]):
        with open(CONFIG["files"]["perf_counter_file"], 'w') as f:
            f.write("0")
        print(f"  ✓ Created {CONFIG['files']['perf_counter_file']}")
    
    print("[✓] All data files initialized!\n")

# ======================== PORTFOLIO ========================
def load_portfolio():
    pf = CONFIG["files"]["portfolio_file"]
    if os.path.exists(pf):
        try:
            with open(pf) as f: return json.load(f)
        except: pass
    return {"balance": 1000.0}

def save_portfolio(p):
    tmp = CONFIG["files"]["portfolio_file"] + ".tmp"
    with open(tmp, 'w') as f: json.dump(p, f, indent=2)
    os.replace(tmp, CONFIG["files"]["portfolio_file"])

portfolio = load_portfolio()

# ======================== FILE MANAGEMENT ========================
def load_open_trades():
    filepath = CONFIG["files"]["open_trades_file"]
    if os.path.exists(filepath):
        try:
            with open(filepath) as f: return json.load(f)
        except: pass
    return []

def save_open_trades(trades):
    filepath = CONFIG["files"]["open_trades_file"]
    tmp = filepath + ".tmp"
    with open(tmp, 'w') as f: json.dump(trades, f, indent=2)
    os.replace(tmp, filepath)

def safe_append_csv(filepath, df_new):
    tmp = filepath + ".tmp"
    try:
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            existing = pd.read_csv(filepath)
            updated = pd.concat([existing, df_new], ignore_index=True)
        else:
            updated = df_new
        updated.to_csv(tmp, index=False)
        os.replace(tmp, filepath)
    except Exception as e:
        print(f"[!] CSV append failed: {e}")
        header = not os.path.exists(filepath) or os.path.getsize(filepath) == 0
        df_new.to_csv(filepath, mode='a', header=header, index=False)

# ======================== COIN UNIVERSE ========================
def fetch_top_liquid_coins():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency":"usd", "order":"market_cap_desc",
              "per_page":100, "page":1, "sparkline":False}
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        symbols = []
        blacklist = set(CONFIG["universe"]["blacklist"])
        for coin in data:
            sym = coin.get("symbol","").upper()
            if sym and sym not in blacklist:
                ys = f"{sym}-USD"
                if ys not in symbols:
                    symbols.append(ys)
        return symbols[:CONFIG["universe"]["limit"]]
    except Exception as e:
        print(f"[!] CoinGecko failed: {e}. Using fallback.")
        return ["BTC-USD","ETH-USD","SOL-USD","BNB-USD","XRP-USD",
                "ADA-USD","DOGE-USD","AVAX-USD","DOT-USD","LINK-USD"]

# ======================== DATA FETCHING ========================
def get_kucoin_klines(kucoin_sym, interval, days=14):
    interval_map = {'1h': '1hour', '4h': '4hour', '1d': '1day'}
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)
    url = "https://api.kucoin.com/api/v1/market/candles"
    params = {
        "type": interval_map.get(interval, interval),
        "symbol": kucoin_sym,
        "startAt": int(start_time.timestamp()),
        "endAt": int(end_time.timestamp()),
    }
    for attempt in range(3):
        try:
            time.sleep(0.15)
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 429:
                time.sleep(2 ** attempt); continue
            body = resp.json()
            if body.get("code") != "200000": return pd.DataFrame()
            candles = body.get("data", [])
            if not candles: return pd.DataFrame()
            rows = []
            for c in candles:
                rows.append({
                    'open_time': datetime.fromtimestamp(int(c[0]), tz=timezone.utc),
                    'Open': float(c[1]), 'Close': float(c[2]),
                    'High': float(c[3]), 'Low': float(c[4]), 'Volume': float(c[5])
                })
            df = pd.DataFrame(rows).set_index('open_time').sort_index()
            return df[['Open','High','Low','Close','Volume']]
        except:
            time.sleep(1)
    return pd.DataFrame()

def get_current_price(kucoin_sym):
    url = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={kucoin_sym}"
    try:
        resp = requests.get(url, timeout=5)
        data = resp.json()
        if data.get("code") == "200000":
            return float(data["data"]["price"])
    except: pass
    return None

def fetch_all_data(coins):
    results = {}
    def fetch_one(yahoo_sym):
        base = yahoo_sym.replace("-USD", "")
        kucoin_sym = f"{base}-USDT"
        df = get_kucoin_klines(kucoin_sym, '4h', days=21)
        return yahoo_sym, df

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_one, sym): sym for sym in coins}
        for future in as_completed(futures):
            try:
                sym, df = future.result()
                if not df.empty and len(df) >= 50:
                    results[sym] = df
            except: pass
    return results

# ======================== QUANT FACTORS ========================
def factor_clv_pressure(df, lookback=12):
    if len(df) < lookback: return 0.0
    recent = df.tail(lookback)
    h, l, c, v = recent['High'], recent['Low'], recent['Close'], recent['Volume']
    candle_range = (h - l).replace(0, 1e-9)
    clv = ((c - l) - (h - c)) / candle_range
    vol_weighted_clv = (clv * v).sum()
    total_vol = v.sum()
    if total_vol == 0: return 0.0
    return float(np.clip(vol_weighted_clv / total_vol, -1, 1))

def factor_volatility_regime(df, lookback=50):
    if len(df) < lookback: return 0.0
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift()).abs(),
        (df['Low'] - df['Close'].shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().dropna()
    if len(atr) < 20: return 0.0
    current_atr = atr.iloc[-1]
    percentile = (atr < current_atr).sum() / len(atr) * 100
    if 30 <= percentile <= 70: return 1.0
    return -0.5

def factor_cross_sectional_momentum(all_returns, target_sym):
    if target_sym not in all_returns or len(all_returns) < 10: return 0.0
    values = list(all_returns.values())
    mean_ret = np.mean(values)
    std_ret = np.std(values)
    if std_ret < 1e-9: return 0.0
    return float((all_returns[target_sym] - mean_ret) / std_ret)

def calculate_atr(df, period=14):
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift()).abs(),
        (df['Low'] - df['Close'].shift()).abs()
    ], axis=1).max(axis=1)
    atr_val = tr.rolling(period).mean().iloc[-1]
    return float(atr_val) if not pd.isna(atr_val) else None

def get_btc_regime(btc_df):
    if btc_df.empty or len(btc_df) < 50: return "NEUTRAL"
    ema50 = btc_df['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
    current = btc_df['Close'].iloc[-1]
    if current > ema50 * 1.01: return "BULLISH"
    if current < ema50 * 0.99: return "BEARISH"
    return "NEUTRAL"

# ======================== SIGNAL GENERATION ========================
def generate_signals():
    coins = fetch_top_liquid_coins()
    print(f"\n[*] Scanning {len(coins)} coins...")

    btc_df = get_kucoin_klines("BTC-USDT", '4h', days=21)
    regime = get_btc_regime(btc_df)
    print(f"[*] BTC Regime: {regime}")

    all_data = fetch_all_data(coins)
    print(f"[*] Got valid data for {len(all_data)} coins")

    if len(all_data) < 10:
        print("[!] Not enough data. Aborting.")
        return [], [], regime

    all_returns = {}
    for sym, df in all_data.items():
        if len(df) >= 12:
            ret = (df['Close'].iloc[-1] / df['Close'].iloc[-12]) - 1
            all_returns[sym] = ret

    scored = []
    for sym, df in all_data.items():
        price = float(df['Close'].iloc[-1])
        clv = factor_clv_pressure(df)
        vol = factor_volatility_regime(df)
        mom_z = factor_cross_sectional_momentum(all_returns, sym)

        direction = "LONG" if mom_z > 0 else "SHORT"

        regime_penalty = 0.0
        if regime == "BULLISH" and direction == "SHORT": regime_penalty = -0.3
        if regime == "BEARISH" and direction == "LONG": regime_penalty = -0.3

        score = (mom_z * 0.6) + (clv * np.sign(mom_z) * 0.3) + (vol * 0.1) + regime_penalty

        atr_val = calculate_atr(df)
        if atr_val is None: atr_val = price * 0.02

        scored.append({
            "symbol": sym, "price": price, "score": round(score, 3),
            "direction": direction, "atr": atr_val,
            "mom_z": round(mom_z, 2), "clv": round(clv, 2),
            "vol_regime": round(vol, 2), "regime": regime,
        })

    scored.sort(key=lambda x: abs(x["score"]), reverse=True)
    signals = []
    for s in scored:
        if abs(s["score"]) >= CONFIG["trading"]["min_score_to_enter"]:
            sig = build_signal(s)
            signals.append(sig)
        if len(signals) >= CONFIG["trading"]["max_signals"]:
            break

    return signals, scored[:10], regime

def build_signal(s):
    direction = s["direction"]
    price = s["price"]
    atr_val = s["atr"]

    entry = price * (0.999 if direction == "LONG" else 1.001)
    stop_dist = max(CONFIG["trading"]["atr_stop_multiplier"] * atr_val, entry * 0.015)
    stop = entry - stop_dist if direction == "LONG" else entry + stop_dist
    risk = abs(entry - stop)

    tps = []
    for m in CONFIG["trading"]["tp_multipliers"]:
        tp = entry + m * risk if direction == "LONG" else entry - m * risk
        tps.append(round(tp, 6))

    balance = portfolio["balance"]
    risk_dollars = balance * CONFIG["trading"]["risk_per_trade_pct"] / 100
    qty = risk_dollars / risk
    notional = qty * entry

    abs_score = abs(s["score"])
    if abs_score >= 2.5: strength = "🟢 STRONG"
    elif abs_score >= 2.0: strength = "🟡 GOOD"
    elif abs_score >= 1.5: strength = "🟠 MODERATE"
    else: strength = "⚪ WEAK"

    return {
        "symbol": s["symbol"], "direction": direction, "strength": strength,
        "entry": round(entry, 6), "stop": round(stop, 6), "tps": tps,
        "qty": round(qty, 6), "notional": round(notional, 2),
        "risk_dollars": round(risk_dollars, 2), "risk_pct": round(risk/entry*100, 2),
        "score": s["score"], "mom_z": s["mom_z"], "clv": s["clv"],
        "vol_regime": s["vol_regime"], "regime": s["regime"],
        "atr": atr_val,
    }

# ======================== TRADE MONITORING + JOURNALING ========================
def monitor_open_trades():
    trades = load_open_trades()
    if not trades:
        print("[*] No open trades to monitor")
        return

    print(f"\n[*] Monitoring {len(trades)} open trades...")
    alerts = []
    closed_trades = []

    for trade in trades:
        sym = trade["symbol"]
        base = sym.replace("-USD", "")
        kucoin_sym = f"{base}-USDT"
        direction = trade["direction"]
        entry = trade["entry"]
        current_stop = trade["current_stop"]
        tps = trade["tps"]
        highest_tp_hit = trade["highest_tp_hit"]
        atr = trade["atr"]
        qty = trade["qty"]

        current_price = get_current_price(kucoin_sym)
        if current_price is None:
            print(f"[!] Could not fetch price for {sym}")
            continue

        new_tp_hit = False
        for i in range(highest_tp_hit + 1, len(tps)):
            tp = tps[i]
            tp_hit = False
            if direction == "LONG" and current_price >= tp:
                tp_hit = True
            elif direction == "SHORT" and current_price <= tp:
                tp_hit = True

            if tp_hit:
                trade["highest_tp_hit"] = i
                new_tp_hit = True

                if i == 0:
                    new_stop = entry
                    stop_msg = f"🔒 Move stop to BREAKEVEN: `${entry:.4f}`"
                elif i == 1:
                    new_stop = tps[0]
                    stop_msg = f"🔒 Move stop to TP1: `${tps[0]:.4f}`"
                elif i >= 2:
                    if direction == "LONG":
                        trail_stop = current_price - (CONFIG["trading"]["trailing_atr_multiplier"] * atr)
                        new_stop = max(trail_stop, tps[i-1])
                    else:
                        trail_stop = current_price + (CONFIG["trading"]["trailing_atr_multiplier"] * atr)
                        new_stop = min(trail_stop, tps[i-1])
                    stop_msg = f"📈 Trail stop to: `${new_stop:.4f}`"

                trade["current_stop"] = new_stop

                if direction == "LONG":
                    pnl_pct = (current_price - entry) / entry * 100
                else:
                    pnl_pct = (entry - current_price) / entry * 100

                alert = (
                    f"🎯 **{base} {direction}** - TP{i+1} HIT!\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 Current: `${current_price:.4f}` | PnL: `{pnl_pct:+.2f}%`\n"
                    f"{stop_msg}\n"
                    f"📋 Close {CONFIG['trading']['fractions'][i]*100:.0f}% | {len(tps)-i-1} TPs left"
                )
                alerts.append(alert)
                print(f"[✓] {base} hit TP{i+1}")

        stop_hit = False
        if direction == "LONG" and current_price <= current_stop:
            stop_hit = True
        elif direction == "SHORT" and current_price >= current_stop:
            stop_hit = True

        if stop_hit:
            exit_price = current_stop
            if direction == "LONG":
                pnl_pct = (exit_price - entry) / entry * 100
            else:
                pnl_pct = (entry - exit_price) / entry * 100

            r_multiple = pnl_pct / (abs(entry - trade["stop"]) / entry * 100)
            pnl_dollars = trade["notional"] * (pnl_pct / 100)

            hit_level = "STOP" if highest_tp_hit == -1 else f"STOP after TP{highest_tp_hit+1}"

            result = {
                "open_time": trade["opened_at"],
                "close_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": sym, "direction": direction,
                "entry": entry, "stop": trade["stop"],
                "tp1": tps[0], "tp2": tps[1], "tp3": tps[2], "tp4": tps[3], "tp5": tps[4],
                "exit_price": exit_price, "qty": qty,
                "pnl_pct": round(pnl_pct, 2), "pnl_dollars": round(pnl_dollars, 2),
                "r_multiple": round(r_multiple, 2), "hit_level": hit_level,
                "score": trade.get("score", 0), "mom_z": trade.get("mom_z", 0), "clv": trade.get("clv", 0)
            }
            closed_trades.append(result)

            alert = (
                f"🛑 **{base} {direction}** - {hit_level}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Exit: `${exit_price:.4f}` | PnL: `{pnl_pct:+.2f}%` ({r_multiple:+.2f}R)\n"
                f"💵 P&L: `${pnl_dollars:+.2f}`"
            )
            alerts.append(alert)
            print(f"[✗] {base} stopped out at ${exit_price:.4f}")

        if highest_tp_hit == len(tps) - 1 and not stop_hit:
            exit_price = tps[-1]
            if direction == "LONG":
                pnl_pct = (exit_price - entry) / entry * 100
            else:
                pnl_pct = (entry - exit_price) / entry * 100

            r_multiple = pnl_pct / (abs(entry - trade["stop"]) / entry * 100)
            pnl_dollars = trade["notional"] * (pnl_pct / 100)

            result = {
                "open_time": trade["opened_at"],
                "close_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": sym, "direction": direction,
                "entry": entry, "stop": trade["stop"],
                "tp1": tps[0], "tp2": tps[1], "tp3": tps[2], "tp4": tps[3], "tp5": tps[4],
                "exit_price": exit_price, "qty": qty,
                "pnl_pct": round(pnl_pct, 2), "pnl_dollars": round(pnl_dollars, 2),
                "r_multiple": round(r_multiple, 2), "hit_level": f"TP{len(tps)}",
                "score": trade.get("score", 0), "mom_z": trade.get("mom_z", 0), "clv": trade.get("clv", 0)
            }
            closed_trades.append(result)

            alert = (
                f"🎉 **{base} {direction}** - ALL TPs HIT!\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Exit: `${exit_price:.4f}` | PnL: `{pnl_pct:+.2f}%` ({r_multiple:+.2f}R)\n"
                f"💵 P&L: `${pnl_dollars:+.2f}`"
            )
            alerts.append(alert)
            print(f"[🎉] {base} hit all TPs!")

    if closed_trades:
        df_results = pd.DataFrame(closed_trades)
        safe_append_csv(CONFIG["files"]["trade_results_file"], df_results)

        closed_symbols = {t["symbol"] for t in closed_trades}
        trades = [t for t in trades if t["symbol"] not in closed_symbols]
        save_open_trades(trades)

        check_performance_report()

    for alert in alerts:
        send_discord(alert)

    if not closed_trades:
        save_open_trades(trades)

def check_performance_report():
    filepath = CONFIG["files"]["trade_results_file"]
    if not os.path.exists(filepath): return

    try:
        df = pd.read_csv(filepath)
        if df.empty: return

        total_trades = len(df)
        counter_file = CONFIG["files"]["perf_counter_file"]
        last_reported = 0
        if os.path.exists(counter_file):
            with open(counter_file) as f:
                try: last_reported = int(f.read().strip())
                except: pass

        milestone = (total_trades // CONFIG["report_every_n_trades"]) * CONFIG["report_every_n_trades"]
        if milestone <= last_reported or milestone == 0: return

        wins = df[df["pnl_dollars"] > 0]
        losses = df[df["pnl_dollars"] < 0]
        total_wins = len(wins)
        total_losses = len(losses)
        winrate = (total_wins / total_trades * 100) if total_trades > 0 else 0

        total_pnl = df["pnl_dollars"].sum()
        avg_r = df["r_multiple"].mean()

        profit_factor = wins["pnl_dollars"].sum() / abs(losses["pnl_dollars"].sum()) if total_losses > 0 else float('inf')

        tp1_hits = len(df[df["hit_level"].str.contains("TP1", na=False)])
        tp2_hits = len(df[df["hit_level"].str.contains("TP2|TP3|TP4|TP5", na=False)])

        report = (
            f"📊 **Performance Report** – {total_trades} Trades\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Total P&L: `${total_pnl:+.2f}`\n"
            f"📈 Win Rate: `{winrate:.1f}%` ({total_wins}W / {total_losses}L)\n"
            f"📊 Profit Factor: `{profit_factor:.2f}`\n"
            f"🎯 Avg R-Multiple: `{avg_r:+.2f}R`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 TP1 Hit Rate: `{tp1_hits}/{total_trades}` ({tp1_hits/total_trades*100:.1f}%)\n"
            f"🎯 TP2+ Hit Rate: `{tp2_hits}/{total_trades}` ({tp2_hits/total_trades*100:.1f}%)\n"
        )

        send_discord(report)
        print(f"\n{report}")

        with open(counter_file, 'w') as f:
            f.write(str(milestone))

    except Exception as e:
        print(f"[!] Report generation failed: {e}")

def show_performance_report():
    filepath = CONFIG["files"]["trade_results_file"]
    if not os.path.exists(filepath):
        print("[!] No trade results yet.")
        return

    df = pd.read_csv(filepath)
    if df.empty:
        print("[!] No trades closed yet.")
        return

    total_trades = len(df)
    wins = df[df["pnl_dollars"] > 0]
    losses = df[df["pnl_dollars"] < 0]
    total_wins = len(wins)
    total_losses = len(losses)
    winrate = (total_wins / total_trades * 100) if total_trades > 0 else 0

    total_pnl = df["pnl_dollars"].sum()
    avg_r = df["r_multiple"].mean()
    profit_factor = wins["pnl_dollars"].sum() / abs(losses["pnl_dollars"].sum()) if total_losses > 0 else float('inf')

    print(f"\n{'='*55}")
    print(f"  PERFORMANCE REPORT – {total_trades} Trades")
    print(f"{'='*55}")
    print(f"  Total P&L: ${total_pnl:+.2f}")
    print(f"  Win Rate: {winrate:.1f}% ({total_wins}W / {total_losses}L)")
    print(f"  Profit Factor: {profit_factor:.2f}")
    print(f"  Avg R-Multiple: {avg_r:+.2f}R")
    print(f"{'='*55}\n")

# ======================== CHARTING ========================
def generate_chart(sig):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import mplfinance as mpf
    except ImportError:
        print("[!] matplotlib/mplfinance not installed. Skipping chart.")
        return None

    sym = sig["symbol"]
    base = sym.replace("-USD", "")
    df = get_kucoin_klines(f"{base}-USDT", '4h', days=21)
    if df.empty or len(df) < 20: return None

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    style = mpf.make_mpf_style(
        base_mpf_style='nightclouds', facecolor='#0d1117',
        gridcolor='#1c2333',
        rc={'axes.labelcolor':'#c9d1d9', 'xtick.color':'#8b949e',
            'ytick.color':'#8b949e', 'axes.titlecolor':'#f0f6fc'}
    )

    ema50 = df['Close'].ewm(span=min(50, len(df)), adjust=False).mean()
    ema20 = df['Close'].ewm(span=min(20, len(df)), adjust=False).mean()
    addplots = [
        mpf.make_addplot(ema50, color='#f39c12', width=1.2),
        mpf.make_addplot(ema20, color='#3498db', width=1.0),
    ]

    title = f"{base}/USDT 4H  |  {sig['direction']}  |  Score: {sig['score']}"
    fig, axes = mpf.plot(df, type='candle', style=style, volume=True,
                         title=title, ylabel='Price', ylabel_lower='Vol',
                         addplot=addplots, returnfig=True, figsize=(10, 7))

    ax = axes[0]

    ax.axhline(y=sig['entry'], color='#f1c40f', linestyle='-', linewidth=2, label=f"Entry: {sig['entry']:.4f}")
    ax.axhline(y=sig['stop'], color='#e74c3c', linestyle='--', linewidth=2, label=f"Stop: {sig['stop']:.4f}")
    colors = ['#2ecc71', '#27ae60', '#1abc9c', '#16a085', '#0e8c72']
    for i, tp in enumerate(sig['tps']):
        frac = CONFIG['trading']['fractions'][i] * 100
        label = f"TP{i+1}: {tp:.4f} ({frac}%)" if i < 2 else None
        ax.axhline(y=tp, color=colors[i], linestyle='--', linewidth=1, alpha=0.8, label=label)

    ax.legend(loc='upper left', facecolor='#0d1117', edgecolor='#30363d',
              labelcolor='#c9d1d9', fontsize=8)

    path = f"chart_{base}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.png"
    fig.savefig(path, dpi=150, bbox_inches='tight', facecolor='#0d1117')
    plt.close(fig)
    return path

# ======================== DISCORD ========================
def send_discord(text):
    if not DISCORD_WEBHOOK_URL: return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": text[:2000]}, timeout=10)
    except: pass

def send_discord_image(image_path, caption=""):
    if not DISCORD_WEBHOOK_URL or not image_path or not os.path.exists(image_path): return
    try:
        with open(image_path, 'rb') as img:
            requests.post(DISCORD_WEBHOOK_URL, data={'content': caption[:2000]},
                          files={'file': img}, timeout=15)
    except: pass

def format_discord_alert(sig):
    icon = "🟢" if sig['direction'] == "LONG" else "🔴"
    tp_lines = ""
    for i, tp in enumerate(sig['tps']):
        frac = CONFIG['trading']['fractions'][i] * 100
        r_mult = CONFIG['trading']['tp_multipliers'][i]
        tp_lines += f"  TP{i+1}: `${tp:.4f}` ({r_mult}R → close {frac:.0f}%)\n"

    msg = (
        f"{icon} **{sig['direction']} {sig['symbol'].replace('-USD','')}** {sig['strength']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Score: `{sig['score']}` | MomZ: `{sig['mom_z']}` | CLV: `{sig['clv']}`\n"
        f"🌊 BTC Regime: `{sig['regime']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Entry: `${sig['entry']:.4f}`\n"
        f"🛑 Stop: `${sig['stop']:.4f}` (-{sig['risk_pct']:.2f}%)\n"
        f"{tp_lines}"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Size: `{sig['qty']}` units (`${sig['notional']:.2f}`)\n"
        f"⚠️ Risk: `${sig['risk_dollars']:.2f}` ({CONFIG['trading']['risk_per_trade_pct']}% of balance)\n"
    )
    return msg

# ======================== SIGNAL LOGGING ========================
def log_signal(sig):
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": sig["symbol"], "direction": sig["direction"],
        "entry": sig["entry"], "stop": sig["stop"],
        "tp1": sig["tps"][0], "tp2": sig["tps"][1], "tp3": sig["tps"][2],
        "score": sig["score"], "mom_z": sig["mom_z"], "clv": sig["clv"],
        "qty": sig["qty"], "notional": sig["notional"],
    }
    filepath = CONFIG["files"]["signal_log"]
    df_new = pd.DataFrame([row])
    safe_append_csv(filepath, df_new)

# ======================== CONSOLE OUTPUT ========================
def print_console_report(signals, top_candidates, regime):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'='*55}")
    print(f"  ALPHASWING v4 – SIGNAL REPORT")
    print(f"  {now}")
    print(f"  BTC Regime: {regime} | Balance: ${portfolio['balance']:.2f}")
    print(f"{'='*55}")

    print(f"\n  TOP CANDIDATES:")
    print(f"  {'#':<3} {'Symbol':<12} {'Dir':<6} {'Score':<7} {'MomZ':<7} {'CLV':<7}")
    print(f"  {'-'*44}")
    for i, c in enumerate(top_candidates[:5]):
        sym = c['symbol'].replace('-USD','')
        print(f"  {i+1:<3} {sym:<12} {c['direction']:<6} {c['score']:<7} {c['mom_z']:<7} {c['clv']:<7}")

    if not signals:
        print(f"\n  ⚪ NO SIGNALS – No coins passed the threshold ({CONFIG['trading']['min_score_to_enter']})")
    else:
        for sig in signals:
            icon = "🟢" if sig['direction'] == "LONG" else "🔴"
            sym = sig['symbol'].replace('-USD','')
            print(f"\n  {icon} {sig['direction']} {sym} {sig['strength']}")
            print(f"  Entry: ${sig['entry']:.4f} | Stop: ${sig['stop']:.4f} (-{sig['risk_pct']:.2f}%)")
            for i, tp in enumerate(sig['tps']):
                print(f"  TP{i+1}: ${tp:.4f} ({CONFIG['trading']['tp_multipliers'][i]}R → close {CONFIG['trading']['fractions'][i]*100:.0f}%)")
            print(f"  Size: {sig['qty']} units (${sig['notional']:.2f}) | Risk: ${sig['risk_dollars']:.2f}")
    print(f"\n{'='*55}\n")

# ======================== MAIN ========================
def run_signals():
    acquire_lock()
    try:
        result = generate_signals()
        if not result or len(result) < 3:
            print("[!] Signal generation failed.")
            return
        signals, top_candidates, regime = result

        print_console_report(signals, top_candidates, regime)

        for sig in signals:
            log_signal(sig)
            chart_path = generate_chart(sig)
            alert = format_discord_alert(sig)
            send_discord(alert)
            if chart_path:
                send_discord_image(chart_path, caption=f"{sig['direction']} {sig['symbol'].replace('-USD','')}")
                try: os.remove(chart_path)
                except: pass

    finally:
        release_lock()

def run_monitor():
    acquire_lock()
    try:
        monitor_open_trades()
    finally:
        release_lock()

def main():
    # Initialize all data files automatically
    initialize_files()

    if "--report" in sys.argv:
        show_performance_report()
    elif "--monitor" in sys.argv:
        print(f"[*] AlphaSwing v4 – Trade Monitor")
        run_monitor()
    elif "--loop" in sys.argv:
        print(f"[*] AlphaSwing v4 – Loop mode")
        while True:
            try:
                run_signals()
                run_monitor()
            except Exception as e:
                print(f"[!] Error: {e}")
            print(f"[*] Sleeping {CONFIG['loop_interval_hours']} hours...")
            time.sleep(CONFIG['loop_interval_hours'] * 3600)
    else:
        run_signals()

if __name__ == "__main__":
    main()