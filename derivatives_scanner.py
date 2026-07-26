#!/usr/bin/env python3
"""
Smart Money Derivatives Scanner (KuCoin Futures Edition)
================================
Scans KuCoin Futures data every 15 minutes and alerts Discord when:
  - Funding rates are extreme (overleveraged market)
  - Open Interest spikes (big move incoming)
  - Price + OI divergence (accumulation/distribution)

Data Source: KuCoin Futures Public API (NO API KEY REQUIRED)
Fix: Corrected API endpoints (/api/v1/allTickers and /api/v1/open-interest-stat) + browser headers.
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
# Map standard names to exact KuCoin Futures contract symbols
SYMBOL_MAP = {
    "BTC": "XBTUSDTM",
    "ETH": "ETHUSDTM",
    "SOL": "SOLUSDTM",
    "BNB": "BNBUSDTM",
    "XRP": "XRPUSDTM",
    "DOGE": "DOGEUSDTM",
    "ADA": "ADAUSDTM",
    "AVAX": "AVAXUSDTM",
    "LINK": "LINKUSDTM",
    "DOT": "DOTUSDTM",
    "MATIC": "POLUSDTM",  # Polygon rebranded to POL on KuCoin
    "LTC": "LTCUSDTM",
    "ATOM": "ATOMUSDTM",
    "NEAR": "NEARUSDTM",
    "APT": "APTUSDTM"
}

CONFIG = {
    "symbols": list(SYMBOL_MAP.keys()),
    "thresholds": {
        "funding_rate_extreme": 0.0005,      # 0.05% (8-hour rate) - extreme leverage
        "funding_rate_critical": 0.001,      # 0.10% - critical squeeze risk
        "oi_spike_pct": 10.0,                # 10% OI increase
        "oi_drop_pct": -10.0,                # -10% OI drop (liquidation cascade)
        "price_oi_divergence": 5.0,          # 5% divergence triggers alert
    },
    "files": {
        "state_file": "derivatives_state.json",
    },
    "api": {
        "base_url": "https://api-futures.kucoin.com",
        "request_delay": 0.15,  # 150ms between requests to be polite
        # CRITICAL: These headers mimic a real browser to bypass the 451 block on GitHub Actions
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.kucoin.com",
            "Referer": "https://www.kucoin.com/futures/"
        }
    },
}

DISCORD_WEBHOOK_URL = os.environ.get("DERIVATIVES_WEBHOOK")

# ==============================================================================
# STATE MANAGEMENT
# ==============================================================================
def load_state():
    filepath = CONFIG["files"]["state_file"]
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Error loading state: {e}")
    return {"oi_history": {}, "last_scan": None}

def save_state(state):
    filepath = CONFIG["files"]["state_file"]
    tmp = filepath + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, filepath)

# ==============================================================================
# KUCOIN FUTURES API FUNCTIONS
# ==============================================================================
def fetch_all_tickers():
    """
    Fetches current tickers (includes funding rate and price) for ALL KuCoin Futures.
    Returns: {standard_symbol: {"funding_rate": float, "price": float, "change_pct": float}}
    """
    # CORRECT ENDPOINT: /api/v1/allTickers
    url = f"{CONFIG['api']['base_url']}/api/v1/allTickers"
    try:
        resp = requests.get(url, headers=CONFIG["api"]["headers"], timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("code") != "200000":
            print(f"[!] KuCoin API Error: {data.get('msg')}")
            return {}
            
        tickers = {}
        for item in data.get("data", []):
            symbol = item.get("symbol")
            # Find which standard symbol this KuCoin symbol belongs to
            standard_sym = next((k for k, v in SYMBOL_MAP.items() if v == symbol), None)
            
            if standard_sym:
                funding_rate = float(item.get("fundingRate", 0))
                last_price = float(item.get("lastPrice", 0))
                # KuCoin returns change as a decimal (e.g., 0.05 for 5%)
                change_pct = float(item.get("changeRate", 0)) * 100 
                
                tickers[standard_sym] = {
                    "kucoin_symbol": symbol,
                    "funding_rate": funding_rate,
                    "price": last_price,
                    "change_pct": change_pct
                }
        return tickers
    except Exception as e:
        print(f"[!] Error fetching KuCoin tickers: {e}")
        return {}

def fetch_open_interest(kucoin_symbol):
    """
    Fetches current open interest for a specific KuCoin Futures symbol.
    Returns: OI value (float)
    """
    # CORRECT ENDPOINT: /api/v1/open-interest-stat
    url = f"{CONFIG['api']['base_url']}/api/v1/open-interest-stat?symbol={kucoin_symbol}"
    try:
        time.sleep(CONFIG["api"]["request_delay"])
        resp = requests.get(url, headers=CONFIG["api"]["headers"], timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("code") == "200000" and data.get("data"):
            values = data["data"].get("values", [])
            if values:
                # The last value in the array is the most recent OI
                return float(values[-1])
        return None
    except Exception as e:
        print(f"[!] Error fetching OI for {kucoin_symbol}: {e}")
        return None

# ==============================================================================
# ANALYSIS FUNCTIONS
# ==============================================================================
def analyze_funding_rate(symbol, rate):
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
    if previous_oi is None or previous_oi == 0:
        return None
    
    change_pct = ((current_oi - previous_oi) / previous_oi) * 100
    thresholds = CONFIG["thresholds"]
    
    if change_pct >= thresholds["oi_spike_pct"]:
        return ("HIGH", f"📈 {symbol}: Open Interest surged {change_pct:+.1f}% — Big move incoming.")
    elif change_pct <= thresholds["oi_drop_pct"]:
        return ("HIGH", f"📉 {symbol}: Open Interest dropped {change_pct:+.1f}% — Liquidation cascade detected!")
    return None

def analyze_price_oi_divergence(symbol, price_change_pct, oi_change_pct):
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
        f"🚨 **SMART MONEY ALERT** — KuCoin Derivatives Scan\n"
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
    print(f"\n[*] Starting KuCoin derivatives scan at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    
    state = load_state()
    oi_history = state.get("oi_history", {})
    
    print("[*] Fetching KuCoin Futures tickers (Funding Rates & Prices)...")
    tickers = fetch_all_tickers()
    print(f"[✓] Got ticker data for {len(tickers)} symbols")
    
    if not tickers:
        print("[!] Failed to fetch any ticker data. Check API or headers.")
        return

    alerts = []
    new_oi_history = {}
    
    for standard_sym in CONFIG["symbols"]:
        if standard_sym not in tickers:
            continue
            
        ticker_data = tickers[standard_sym]
        kucoin_sym = ticker_data["kucoin_symbol"]
        
        print(f"[*] Scanning {standard_sym} ({kucoin_sym})...")
        
        # 1. Analyze funding rate
        result = analyze_funding_rate(standard_sym, ticker_data["funding_rate"])
        if result:
            alerts.append(result)
        
        # 2. Fetch and analyze Open Interest
        current_oi = fetch_open_interest(kucoin_sym)
        if current_oi is not None:
            previous_oi = oi_history.get(standard_sym)
            new_oi_history[standard_sym] = current_oi
            
            if previous_oi is not None:
                result = analyze_oi_change(standard_sym, current_oi, previous_oi)
                if result:
                    alerts.append(result)
        
        # 3. Analyze Price/OI divergence
        if current_oi is not None and oi_history.get(standard_sym):
            oi_change_pct = ((current_oi - oi_history[standard_sym]) / oi_history[standard_sym]) * 100
            result = analyze_price_oi_divergence(standard_sym, ticker_data["change_pct"], oi_change_pct)
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
    print("  SMART MONEY DERIVATIVES SCANNER (KUCOIN EDITION)")
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