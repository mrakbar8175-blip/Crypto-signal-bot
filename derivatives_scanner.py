#!/usr/bin/env python3
"""
Smart Money Derivatives Scanner (Bybit Edition)
================================
Scans Bybit Futures data every 15 minutes and alerts Discord when:
  - Funding rates are extreme (overleveraged market)
  - Open Interest spikes (big move incoming)
  - Long/Short ratio is imbalanced (squeeze risk)
  - Price + OI divergence (accumulation/distribution)

Data Source: Bybit V5 Public API (NO API KEY REQUIRED, GitHub Actions friendly)
"""

import os
import json
import time
import sys
import requests
from datetime import datetime, timezone

# ==============================================================================
# CONFIGURATION
# ==============================================================================
CONFIG = {
    "symbols": [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
        "MATICUSDT", "LTCUSDT", "ATOMUSDT", "NEARUSDT", "APTUSDT"
    ],
    "thresholds": {
        "funding_rate_extreme": 0.0005,      # 0.05% (8-hour rate) - extreme leverage
        "funding_rate_critical": 0.001,      # 0.10% - critical squeeze risk
        "oi_spike_pct": 10.0,                # 10% OI increase in 4 hours
        "oi_drop_pct": -10.0,                # -10% OI drop (liquidation cascade)
        "long_short_extreme": 0.75,          # 75% on one side = squeeze risk
        "price_oi_divergence": 5.0,          # 5% divergence triggers alert
    },
    "files": {
        "state_file": "derivatives_state.json",
    },
    "api": {
        "base_url": "https://api.bybit.com",
        "request_delay": 0.1,  # 100ms between requests to avoid rate limits
        # Crucial: User-Agent prevents GitHub Actions IPs from being auto-blocked
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }
    },
}

DISCORD_WEBHOOK_URL = os.environ.get("DERIVATIVES_WEBHOOK")

# ==============================================================================
# STATE MANAGEMENT
# ==============================================================================
def load_state():
    """Loads the previous state to detect changes over time."""
    filepath = CONFIG["files"]["state_file"]
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Error loading state: {e}")
    return {"oi_history": {}, "last_scan": None}

def save_state(state):
    """Atomically saves the current state."""
    filepath = CONFIG["files"]["state_file"]
    tmp = filepath + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, filepath)

# ==============================================================================
# BYBIT V5 API FUNCTIONS
# ==============================================================================
def fetch_tickers():
    """
    Fetches current tickers (includes funding rate and price) for all linear perpetuals.
    Returns: {symbol: {"funding_rate": float, "price": float}}
    """
    url = f"{CONFIG['api']['base_url']}/v5/market/tickers"
    params = {"category": "linear"}
    
    try:
        resp = requests.get(url, params=params, headers=CONFIG["api"]["headers"], timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("retCode") != 0:
            print(f"[!] Bybit API Error: {data.get('retMsg')}")
            return {}
            
        tickers = {}
        for item in data.get("result", {}).get("list", []):
            symbol = item.get("symbol")
            if symbol in CONFIG["symbols"]:
                tickers[symbol] = {
                    "funding_rate": float(item.get("fundingRate", 0)),
                    "price": float(item.get("lastPrice", 0)),
                    "price_change_pct": float(item.get("price24hPcnt", 0)) * 100
                }
        return tickers
    except Exception as e:
        print(f"[!] Error fetching tickers: {e}")
        return {}

def fetch_open_interest(symbol):
    """
    Fetches current open interest for a specific symbol.
    Returns: OI value (float)
    """
    url = f"{CONFIG['api']['base_url']}/v5/market/open-interest"
    params = {"category": "linear", "symbol": symbol, "intervalTime": "5min"}
    
    try:
        time.sleep(CONFIG["api"]["request_delay"])
        resp = requests.get(url, params=params, headers=CONFIG["api"]["headers"], timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("retCode") == 0 and data.get("result", {}).get("list"):
            # The list is ordered newest first
            return float(data["result"]["list"][0].get("openInterest", 0))
        return None
    except Exception as e:
        print(f"[!] Error fetching OI for {symbol}: {e}")
        return None

def fetch_long_short_ratio(symbol):
    """
    Fetches global long/short account ratio.
    Returns: {"long": float, "short": float}
    """
    url = f"{CONFIG['api']['base_url']}/v5/market/account-ratio"
    params = {"category": "linear", "symbol": symbol, "period": "5min"}
    
    try:
        time.sleep(CONFIG["api"]["request_delay"])
        resp = requests.get(url, params=params, headers=CONFIG["api"]["headers"], timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("retCode") == 0 and data.get("result", {}).get("list"):
            latest = data["result"]["list"][0]
            return {
                "long": float(latest.get("buyRatio", 0)),
                "short": float(latest.get("sellRatio", 0))
            }
        return None
    except Exception as e:
        print(f"[!] Error fetching L/S ratio for {symbol}: {e}")
        return None

# ==============================================================================
# ANALYSIS FUNCTIONS
# ==============================================================================
def analyze_funding_rate(symbol, rate):
    """Analyzes funding rate severity."""
    abs_rate = abs(rate)
    thresholds = CONFIG["thresholds"]
    
    if abs_rate >= thresholds["funding_rate_critical"]:
        direction = "LONG" if rate > 0 else "SHORT"
        return ("CRITICAL", f"🚨 {symbol}: Funding rate at {rate*100:+.3f}% — Extreme {direction} leverage! Squeeze imminent.")
    elif abs_rate >= thresholds["funding_rate_extreme"]:
        direction = "LONG" if rate > 0 else "SHORT"
        return ("HIGH", f"⚠️ {symbol}: Funding rate at {rate*100:+.3f}% — Heavy {direction} positioning.")
    
    return None

def analyze_oi_change(symbol, current_oi, previous_oi):
    """Analyzes Open Interest change percentage."""
    if previous_oi is None or previous_oi == 0:
        return None
    
    change_pct = ((current_oi - previous_oi) / previous_oi) * 100
    thresholds = CONFIG["thresholds"]
    
    if change_pct >= thresholds["oi_spike_pct"]:
        return ("HIGH", f"📈 {symbol}: Open Interest surged {change_pct:+.1f}% — Big move incoming.")
    elif change_pct <= thresholds["oi_drop_pct"]:
        return ("HIGH", f"📉 {symbol}: Open Interest dropped {change_pct:+.1f}% — Liquidation cascade detected!")
    
    return None

def analyze_long_short_ratio(symbol, ls_data):
    """Analyzes long/short ratio imbalance."""
    if ls_data is None:
        return None
    
    long_ratio = ls_data["long"]
    short_ratio = ls_data["short"]
    threshold = CONFIG["thresholds"]["long_short_extreme"]
    
    if long_ratio >= threshold:
        return ("MEDIUM", f"⚖️ {symbol}: {long_ratio*100:.1f}% of accounts are LONG — Squeeze risk if price drops.")
    elif short_ratio >= threshold:
        return ("MEDIUM", f"⚖️ {symbol}: {short_ratio*100:.1f}% of accounts are SHORT — Squeeze risk if price pumps.")
    
    return None

def analyze_price_oi_divergence(symbol, price_change_pct, oi_change_pct):
    """Detects divergence between price movement and OI movement."""
    threshold = CONFIG["thresholds"]["price_oi_divergence"]
    
    # Price relatively flat but OI surging = coiled spring
    if abs(price_change_pct) < 2.0 and oi_change_pct > threshold:
        return ("HIGH", f"🎯 {symbol}: Price flat ({price_change_pct:+.1f}%) but OI surging ({oi_change_pct:+.1f}%) — Accumulation detected!")
    
    # Price rising but OI falling = weak rally, distribution
    if price_change_pct > 3.0 and oi_change_pct < -threshold:
        return ("MEDIUM", f"📊 {symbol}: Price up {price_change_pct:+.1f}% but OI down {oi_change_pct:+.1f}% — Weak rally, distribution.")
    
    # Price falling but OI rising = aggressive shorting
    if price_change_pct < -3.0 and oi_change_pct > threshold:
        return ("MEDIUM", f"📊 {symbol}: Price down {price_change_pct:+.1f}% but OI up {oi_change_pct:+.1f}% — Aggressive shorting.")
    
    return None

# ==============================================================================
# DISCORD ALERT FORMATTING
# ==============================================================================
def format_alert_header():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"🚨 **SMART MONEY ALERT** — Derivatives Scan\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_alert_footer(alert_count):
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 **{alert_count} alert(s) triggered**\n"
        f"💡 **Recommendation:** Check your positions. Tighten stops if overleveraged.\n"
        f"⚡ Scan runs every 15 minutes."
    )

def send_discord_alert(message):
    if not DISCORD_WEBHOOK_URL:
        print("[!] No Discord webhook configured. Skipping alert.")
        return
    
    try:
        if len(message) > 1950:
            message = message[:1950] + "\n... (truncated)"
        
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=10
        )
        
        if resp.status_code in [200, 204]:
            print(f"[✓] Alert sent to Discord successfully")
        else:
            print(f"[!] Discord send failed: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"[!] Discord error: {e}")

# ==============================================================================
# MAIN SCANNER LOGIC
# ==============================================================================
def run_scan():
    print(f"\n[*] Starting derivatives scan at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    
    state = load_state()
    oi_history = state.get("oi_history", {})
    
    print("[*] Fetching market tickers (Funding Rates & Prices)...")
    tickers = fetch_tickers()
    print(f"[✓] Got ticker data for {len(tickers)} symbols")
    
    alerts = []
    new_oi_history = {}
    
    for symbol in CONFIG["symbols"]:
        print(f"[*] Scanning {symbol}...")
        
        # 1. Analyze funding rate
        if symbol in tickers:
            result = analyze_funding_rate(symbol, tickers[symbol]["funding_rate"])
            if result:
                alerts.append(result)
        
        # 2. Fetch and analyze Open Interest
        current_oi = fetch_open_interest(symbol)
        if current_oi is not None:
            previous_oi = oi_history.get(symbol)
            new_oi_history[symbol] = current_oi
            
            if previous_oi is not None:
                result = analyze_oi_change(symbol, current_oi, previous_oi)
                if result:
                    alerts.append(result)
        
        # 3. Fetch and analyze Long/Short ratio
        ls_data = fetch_long_short_ratio(symbol)
        if ls_data:
            result = analyze_long_short_ratio(symbol, ls_data)
            if result:
                alerts.append(result)
        
        # 4. Analyze Price/OI divergence
        if symbol in tickers and current_oi is not None and oi_history.get(symbol):
            price_change = tickers[symbol]["price_change_pct"]
            oi_change_pct = ((current_oi - oi_history[symbol]) / oi_history[symbol]) * 100
            result = analyze_price_oi_divergence(symbol, price_change, oi_change_pct)
            if result:
                alerts.append(result)
    
    # Sort alerts by severity
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
    alerts.sort(key=lambda x: severity_order.get(x[0], 99))
    
    if alerts:
        print(f"\n[!] {len(alerts)} alerts triggered!")
        message = format_alert_header()
        for severity, msg in alerts:
            message += f"{msg}\n"
        message += format_alert_footer(len(alerts))
        
        print(f"\n{message}")
        send_discord_alert(message)
    else:
        print("[✓] No extreme conditions detected. Market is calm.")
    
    state["oi_history"] = new_oi_history
    state["last_scan"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    save_state(state)
    
    print(f"[*] Scan complete. State saved.\n")

# ==============================================================================
# ENTRY POINT
# ==============================================================================
def main():
    print("=" * 60)
    print("  SMART MONEY DERIVATIVES SCANNER (BYBIT EDITION)")
    print("=" * 60)
    
    if not DISCORD_WEBHOOK_URL:
        print("[!] WARNING: DERIVATIVES_WEBHOOK environment variable not set!")
    
    try:
        run_scan()
    except Exception as e:
        print(f"[!] Fatal error during scan: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()