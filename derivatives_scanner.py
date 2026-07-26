#!/usr/bin/env python3
"""
Smart Money Derivatives Scanner
================================
Scans Binance Futures data every 15 minutes and alerts Discord when:
  - Funding rates are extreme (overleveraged market)
  - Open Interest spikes (big move incoming)
  - Long/Short ratio is imbalanced (squeeze risk)
  - Price + OI divergence (accumulation/distribution)

Data Source: Binance Futures Public API (NO API KEY REQUIRED)
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
        "base_url": "https://fapi.binance.com",
        "request_delay": 0.1,  # 100ms between requests to avoid rate limits
    },
}

DISCORD_WEBHOOK_URL = os.environ.get("DERIVATIVES_WEBHOOK")

# ==============================================================================
# STATE MANAGEMENT (Tracks OI changes over time)
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
# BINANCE FUTURES API FUNCTIONS
# ==============================================================================
def fetch_funding_rates():
    """
    Fetches current funding rates for all symbols.
    Returns: {symbol: funding_rate}
    """
    url = f"{CONFIG['api']['base_url']}/fapi/v1/premiumIndex"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        funding_rates = {}
        for item in data:
            symbol = item.get("symbol")
            if symbol in CONFIG["symbols"]:
                rate = float(item.get("lastFundingRate", 0))
                funding_rates[symbol] = rate
        
        return funding_rates
    except Exception as e:
        print(f"[!] Error fetching funding rates: {e}")
        return {}

def fetch_open_interest(symbol):
    """
    Fetches current open interest for a specific symbol.
    Returns: OI in contracts (float)
    """
    url = f"{CONFIG['api']['base_url']}/fapi/v1/openInterest"
    params = {"symbol": symbol}
    
    try:
        time.sleep(CONFIG["api"]["request_delay"])
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("openInterest", 0))
    except Exception as e:
        print(f"[!] Error fetching OI for {symbol}: {e}")
        return None

def fetch_long_short_ratio(symbol, period="5m"):
    """
    Fetches global long/short account ratio.
    Returns: {long_ratio, short_ratio}
    """
    url = f"{CONFIG['api']['base_url']}/futures/data/globalLongShortAccountRatio"
    params = {"symbol": symbol, "period": period, "limit": 1}
    
    try:
        time.sleep(CONFIG["api"]["request_delay"])
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if data and len(data) > 0:
            long_ratio = float(data[0].get("longAccount", 0))
            short_ratio = float(data[0].get("shortAccount", 0))
            return {"long": long_ratio, "short": short_ratio}
        return None
    except Exception as e:
        print(f"[!] Error fetching L/S ratio for {symbol}: {e}")
        return None

def fetch_price_change(symbol):
    """
    Fetches 24h price change percentage.
    Returns: price_change_pct (float)
    """
    url = f"{CONFIG['api']['base_url']}/fapi/v1/ticker/24hr"
    params = {"symbol": symbol}
    
    try:
        time.sleep(CONFIG["api"]["request_delay"])
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("priceChangePercent", 0))
    except Exception as e:
        print(f"[!] Error fetching price for {symbol}: {e}")
        return None

# ==============================================================================
# ANALYSIS FUNCTIONS
# ==============================================================================
def analyze_funding_rate(symbol, rate):
    """
    Analyzes funding rate severity.
    Returns: (severity_level, message) or None if normal
    """
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
    """
    Analyzes Open Interest change percentage.
    Returns: (severity_level, message) or None if normal
    """
    if previous_oi is None or previous_oi == 0:
        return None
    
    change_pct = ((current_oi - previous_oi) / previous_oi) * 100
    thresholds = CONFIG["thresholds"]
    
    if change_pct >= thresholds["oi_spike_pct"]:
        return ("HIGH", f"📈 {symbol}: Open Interest surged {change_pct:+.1f}% — Big move incoming (likely LONG squeeze if price stalls).")
    elif change_pct <= thresholds["oi_drop_pct"]:
        return ("HIGH", f"📉 {symbol}: Open Interest dropped {change_pct:+.1f}% — Liquidation cascade detected!")
    
    return None

def analyze_long_short_ratio(symbol, ls_data):
    """
    Analyzes long/short ratio imbalance.
    Returns: (severity_level, message) or None if balanced
    """
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
    """
    Detects divergence between price movement and OI movement.
    Price flat + OI rising = accumulation (big move coming)
    Price rising + OI falling = distribution (weak rally)
    Returns: (severity_level, message) or None
    """
    threshold = CONFIG["thresholds"]["price_oi_divergence"]
    
    # Price relatively flat but OI surging = coiled spring
    if abs(price_change_pct) < 2.0 and oi_change_pct > threshold:
        return ("HIGH", f"🎯 {symbol}: Price flat ({price_change_pct:+.1f}%) but OI surging ({oi_change_pct:+.1f}%) — Accumulation detected. Volatile move incoming!")
    
    # Price rising but OI falling = weak rally, distribution
    if price_change_pct > 3.0 and oi_change_pct < -threshold:
        return ("MEDIUM", f"📊 {symbol}: Price up {price_change_pct:+.1f}% but OI down {oi_change_pct:+.1f}% — Weak rally, distribution phase.")
    
    # Price falling but OI rising = aggressive shorting
    if price_change_pct < -3.0 and oi_change_pct > threshold:
        return ("MEDIUM", f"📊 {symbol}: Price down {price_change_pct:+.1f}% but OI up {oi_change_pct:+.1f}% — Aggressive shorting, potential short squeeze.")
    
    return None

# ==============================================================================
# DISCORD ALERT FORMATTING
# ==============================================================================
def format_alert_header():
    """Formats the header for the Discord alert."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"🚨 **SMART MONEY ALERT** — Derivatives Scan\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_alert_footer(alert_count):
    """Formats the footer with recommendations."""
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 **{alert_count} alert(s) triggered**\n"
        f"💡 **Recommendation:** Check your positions. Tighten stops if overleveraged.\n"
        f"⚡ Scan runs every 15 minutes."
    )

def send_discord_alert(message):
    """Sends the formatted alert to Discord."""
    if not DISCORD_WEBHOOK_URL:
        print("[!] No Discord webhook configured. Skipping alert.")
        return
    
    try:
        # Truncate if too long (Discord limit is 2000 chars)
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
    """Main scan function that analyzes all symbols and triggers alerts."""
    print(f"\n[*] Starting derivatives scan at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    
    # Load previous state
    state = load_state()
    oi_history = state.get("oi_history", {})
    
    # Fetch funding rates (single API call for all symbols)
    print("[*] Fetching funding rates...")
    funding_rates = fetch_funding_rates()
    print(f"[✓] Got funding rates for {len(funding_rates)} symbols")
    
    # Collect all alerts
    alerts = []
    new_oi_history = {}
    
    # Analyze each symbol
    for symbol in CONFIG["symbols"]:
        print(f"[*] Scanning {symbol}...")
        
        # 1. Analyze funding rate
        if symbol in funding_rates:
            result = analyze_funding_rate(symbol, funding_rates[symbol])
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
        
        # 4. Analyze Price/OI divergence (if we have historical data)
        if symbol in funding_rates and current_oi is not None and oi_history.get(symbol):
            price_change = fetch_price_change(symbol)
            if price_change is not None:
                oi_change_pct = ((current_oi - oi_history[symbol]) / oi_history[symbol]) * 100
                result = analyze_price_oi_divergence(symbol, price_change, oi_change_pct)
                if result:
                    alerts.append(result)
    
    # Sort alerts by severity
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
    alerts.sort(key=lambda x: severity_order.get(x[0], 99))
    
    # Send alert if we have any
    if alerts:
        print(f"\n[!] {len(alerts)} alerts triggered!")
        message = format_alert_header()
        
        # Group by severity
        for severity, msg in alerts:
            message += f"{msg}\n"
        
        message += format_alert_footer(len(alerts))
        
        print(f"\n{message}")
        send_discord_alert(message)
    else:
        print("[✓] No extreme conditions detected. Market is calm.")
    
    # Save new state
    state["oi_history"] = new_oi_history
    state["last_scan"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    save_state(state)
    
    print(f"[*] Scan complete. State saved.\n")

# ==============================================================================
# ENTRY POINT
# ==============================================================================
def main():
    """Main entry point."""
    print("=" * 60)
    print("  SMART MONEY DERIVATIVES SCANNER")
    print("=" * 60)
    
    if not DISCORD_WEBHOOK_URL:
        print("[!] WARNING: DERIVATIVES_WEBHOOK environment variable not set!")
        print("[!] Alerts will be logged to console but NOT sent to Discord.")
    
    try:
        run_scan()
    except Exception as e:
        print(f"[!] Fatal error during scan: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()