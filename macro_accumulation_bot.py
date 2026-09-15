#!/usr/bin/env python3
"""
Macro Accumulation & Value Bot (v1.1)
================================
A long-term investment companion bot that scans for deep value DCA zones 
and macro sentiment shifts. 

NEW FEATURE: Weekly Investment Digest (Every Sunday)

Features:
  - 200-Day SMA Discount Scanner (Identifies historical accumulation zones)
  - Fear & Greed Index Tracker (Contrarian investing alerts)
  - Weekly Investment Digest (Sunday summary of active DCA zones)
  - 3-Channel Clean Routing (Owner Hub, Investment Watch, Macro Digest)

Data Sources: CoinGecko (Free), Alternative.me (Free)
"""

import os
import json
import time
import sys
import requests
import pandas as pd
from datetime import datetime, timezone

# ==============================================================================
# CONFIGURATION
# ==============================================================================
# CoinGecko IDs for the top fundamentally strong assets
COIN_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "DOT": "polkadot",
    "LTC": "litecoin",
    "ATOM": "cosmos",
    "NEAR": "near",
    "APT": "aptos",
    "UNI": "uniswap",
    "FIL": "filecoin"
}

CONFIG = {
    "dca_thresholds": {
        "deep_value": 0.70,  # 30% below 200 SMA (Extreme DCA Zone)
        "fair_value": 0.85   # 15% below 200 SMA (Standard DCA Zone)
    },
    "fear_greed_thresholds": {
        "extreme_fear": 25,
        "extreme_greed": 75
    },
    "weekly_reminder": {
        "enabled": True,        # Set to False to disable weekly reminders
        "day_of_week": 6,       # 0=Monday, 6=Sunday (6 = Sunday)
        "time_utc": 14          # 14:00 UTC (2 PM UTC)
    },
    "files": {
        "state_file": "accumulation_state.json"
    },
    "api": {
        "coingecko_base": "https://api.coingecko.com/api/v3",
        "fear_greed_url": "https://api.alternative.me/fng/?limit=1",
        "request_delay": 6.5  # 6.5 seconds to respect CoinGecko free tier (10 req/min)
    }
}

# Webhooks for 3-Channel Routing
WEBHOOK_OWNER_HUB = os.environ.get("WEBHOOK_OWNER_HUB")
WEBHOOK_INVESTMENT_WATCH = os.environ.get("WEBHOOK_INVESTMENT_WATCH")
WEBHOOK_MACRO_DIGEST = os.environ.get("WEBHOOK_MACRO_DIGEST")

# ==============================================================================
# STATE MANAGEMENT
# ==============================================================================
def load_state():
    filepath = CONFIG["files"]["state_file"]
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_fear_greed": None, "alerted_dca": [], "last_weekly_reminder": None}

def save_state(state):
    filepath = CONFIG["files"]["state_file"]
    tmp = filepath + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, filepath)

# ==============================================================================
# API FUNCTIONS
# ==============================================================================
def fetch_fear_and_greed():
    """Fetches the current Crypto Fear & Greed Index."""
    try:
        resp = requests.get(CONFIG["api"]["fear_greed_url"], timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return int(data["data"][0]["value"]), data["data"][0]["value_classification"]
    except Exception as e:
        print(f"[!] Error fetching Fear & Greed: {e}")
        return None, None

def fetch_200_day_data(coin_id):
    """Fetches 200 days of daily price data from CoinGecko to calculate SMA."""
    url = f"{CONFIG['api']['coingecko_base']}/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": 200, "interval": "daily"}
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        prices = data.get("prices", [])
        if len(prices) < 200:
            return None, None
            
        # Convert to pandas DataFrame to easily calculate SMA
        df = pd.DataFrame(prices, columns=["timestamp", "price"])
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms")
        
        # Calculate 200-Day Simple Moving Average
        df["sma_200"] = df["price"].rolling(window=200).mean()
        
        current_price = df["price"].iloc[-1]
        sma_200 = df["sma_200"].iloc[-1]
        
        return current_price, sma_200
    except Exception as e:
        print(f"[!] Error fetching data for {coin_id}: {e}")
        return None, None

# ==============================================================================
# ANALYSIS & FORMATTING
# ==============================================================================
def analyze_dca_opportunity(symbol, current_price, sma_200):
    """Determines if a coin is in a DCA zone based on its 200-Day SMA."""
    if not current_price or not sma_200 or sma_200 == 0:
        return None, 0
        
    discount_pct = ((current_price - sma_200) / sma_200) * 100
    
    thresholds = CONFIG["dca_thresholds"]
    
    if current_price <= (sma_200 * thresholds["deep_value"]):
        return "DEEP_VALUE", discount_pct
    elif current_price <= (sma_200 * thresholds["fair_value"]):
        return "FAIR_VALUE", discount_pct
        
    return None, discount_pct

def format_owner_log(symbol, current_price, sma_200, discount_pct, zone):
    """Verbose message for the Owner Hub."""
    zone_str = zone if zone else "NO ZONE"
    return (
        f"📊 **DATA LOG: {symbol}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Current Price: `${current_price:,.2f}`\n"
        f"📈 200-Day SMA: `${sma_200:,.2f}`\n"
        f" Discount: `{discount_pct:.1f}%`\n"
        f"🎯 Zone: `{zone_str}`\n"
    )

def format_clean_dca_alert(symbol, current_price, sma_200, discount_pct, zone):
    """Clean, actionable message for the Investment Watch channel."""
    emoji = "" if zone == "DEEP_VALUE" else "📉"
    zone_name = "Extreme Accumulation Zone" if zone == "DEEP_VALUE" else "Standard DCA Zone"
    
    return (
        f"{emoji} **DCA OPPORTUNITY: {symbol}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Current Price: `${current_price:,.2f}`\n"
        f" 200-Day SMA: `${sma_200:,.2f}`\n"
        f"📉 Discount: **{discount_pct:.1f}%**\n"
        f"🎯 Status: **{zone_name}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Historically, buying strong assets at a discount to their 200-day SMA yields high long-term returns. Consider scaling in.*"
    )

def format_weekly_digest(coin_data_list):
    """
    Formats a weekly digest showing all coins currently in DCA zones.
    coin_data_list: List of dicts with symbol, price, sma_200, discount_pct, zone
    """
    if not coin_data_list:
        return (
            f"📊 **WEEKLY INVESTMENT DIGEST**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ **Market Status:** All tracked assets are trading at or above fair value.\n"
            f"💡 **Action:** Stick to your regular DCA schedule. No extreme discounts available.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f" *Patience is a virtue in investing. Wait for the market to come to you.*"
        )
    
    # Separate deep value and fair value coins
    deep_value_coins = [c for c in coin_data_list if c["zone"] == "DEEP_VALUE"]
    fair_value_coins = [c for c in coin_data_list if c["zone"] == "FAIR_VALUE"]
    
    message = (
        f"📊 **WEEKLY INVESTMENT DIGEST**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 **Active DCA Opportunities:** {len(coin_data_list)} coins\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    
    # List Deep Value coins first
    if deep_value_coins:
        message += f"\n🔥 **EXTREME ACCUMULATION ZONES** ({len(deep_value_coins)} coins)\n"
        for coin in sorted(deep_value_coins, key=lambda x: x["discount_pct"]):
            message += (
                f"\n{coin['symbol']}\n"
                f"  💰 Price: `${coin['price']:,.2f}`\n"
                f"   Discount: **{coin['discount_pct']:.1f}%** below 200-day SMA\n"
                f"  🎯 *Historical deep value zone*"
            )
    
    # List Fair Value coins
    if fair_value_coins:
        message += f"\n\n📉 **STANDARD DCA ZONES** ({len(fair_value_coins)} coins)\n"
        for coin in sorted(fair_value_coins, key=lambda x: x["discount_pct"]):
            message += (
                f"\n{coin['symbol']}\n"
                f"  💰 Price: `${coin['price']:,.2f}`\n"
                f"  📉 Discount: **{coin['discount_pct']:.1f}%** below 200-day SMA"
            )
    
    message += (
        f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 **Weekly Strategy:**\n"
        f"  • Scale into deep value positions aggressively\n"
        f"  • Maintain regular DCA on fair value assets\n"
        f"  • Keep dry powder for further dips\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f" *Time in the market beats timing the market. Stay disciplined.*"
    )
    
    return message

def format_fear_greed_alert(value, classification, previous_value):
    """Clean alert for the Macro Digest channel."""
    if value <= CONFIG["fear_greed_thresholds"]["extreme_fear"]:
        emoji = ""
        action = "Be greedy when others are fearful. This is a historical accumulation zone."
    elif value >= CONFIG["fear_greed_thresholds"]["extreme_greed"]:
        emoji = ""
        action = "Be fearful when others are greedy. Consider taking long-term profits or pausing buys."
    else:
        emoji = "️"
        action = "Market is neutral. Stick to your standard DCA schedule."

    change_str = ""
    if previous_value is not None:
        diff = value - previous_value
        change_str = f" (Changed by {diff:+d} since last scan)"

    return (
        f"{emoji} **MACRO SENTIMENT UPDATE**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌡️ **Fear & Greed Index:** `{value}` ({classification}){change_str}\n"
        f"💡 **Action:** {action}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ *Ignore the noise. Focus on the macro trend.*"
    )

def send_discord(webhook_url, message):
    """Sends a message to a specific Discord webhook."""
    if not webhook_url:
        return
    try:
        if len(message) > 1950:
            message = message[:1950] + "\n... (truncated)"
        resp = requests.post(webhook_url, json={"content": message}, timeout=10)
        if resp.status_code not in [200, 204]:
            print(f"[!] Discord send failed: {resp.status_code}")
    except Exception as e:
        print(f"[!] Discord error: {e}")

# ==============================================================================
# MAIN SCANNER LOGIC
# ==============================================================================
def run_scan():
    print(f"\n[*] Starting Macro Accumulation Scan at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    
    state = load_state()
    last_fg = state.get("last_fear_greed")
    owner_logs = []
    dca_alerts = []
    current_dca_coins = []  # Track all coins in DCA zones for weekly digest
    
    # Check if today is weekly reminder day
    now = datetime.now(timezone.utc)
    is_weekly_reminder_day = (
        CONFIG["weekly_reminder"]["enabled"] and
        now.weekday() == CONFIG["weekly_reminder"]["day_of_week"] and
        now.hour == CONFIG["weekly_reminder"]["time_utc"]
    )
    
    if is_weekly_reminder_day:
        print("[*] Weekly reminder day detected!")

    # 1. FEAR & GREED SCAN
    print("[*] Fetching Fear & Greed Index...")
    fg_value, fg_class = fetch_fear_and_greed()
    
    if fg_value is not None:
        # Alert if it crosses a threshold or changes significantly
        should_alert = False
        if last_fg is None:
            should_alert = True
        elif (last_fg < 25 and fg_value >= 25) or (last_fg > 25 and fg_value <= 25):
            should_alert = True # Crossed Extreme Fear
        elif (last_fg < 75 and fg_value >= 75) or (last_fg > 75 and fg_value <= 75):
            should_alert = True # Crossed Extreme Greed
        elif abs(fg_value - last_fg) >= 10:
            should_alert = True # Significant daily shift

        if should_alert:
            msg = format_fear_greed_alert(fg_value, fg_class, last_fg)
            print(f"[!] Fear & Greed Alert Triggered: {fg_value}")
            send_discord(WEBHOOK_MACRO_DIGEST, msg)
            owner_logs.append(f"️ Fear & Greed Alert Sent: {fg_value} ({fg_class})")
        
        state["last_fear_greed"] = fg_value

    # 2. 200-DAY SMA DCA SCAN
    print("[*] Scanning Top Coins for 200-Day SMA Discounts...")
    for symbol, coin_id in COIN_MAP.items():
        print(f"[*] Analyzing {symbol}...")
        
        current_price, sma_200 = fetch_200_day_data(coin_id)
        
        if current_price and sma_200:
            zone, discount_pct = analyze_dca_opportunity(symbol, current_price, sma_200)
            
            # Always log to Owner Hub
            owner_logs.append(format_owner_log(symbol, current_price, sma_200, discount_pct, zone))
            
            # Track for weekly digest
            if zone:
                current_dca_coins.append({
                    "symbol": symbol,
                    "price": current_price,
                    "sma_200": sma_200,
                    "discount_pct": discount_pct,
                    "zone": zone
                })
            
            # Alert Investment Watch if in a DCA zone (not spamming)
            if zone:
                # Prevent spamming the same coin every day if it stays in the zone
                alert_key = f"{symbol}_{zone}"
                if alert_key not in state.get("alerted_dca", []):
                    msg = format_clean_dca_alert(symbol, current_price, sma_200, discount_pct, zone)
                    print(f"[!] DCA Alert Triggered for {symbol}: {zone}")
                    send_discord(WEBHOOK_INVESTMENT_WATCH, msg)
                    
                    # Add to alerted list (keep list manageable)
                    if "alerted_dca" not in state:
                        state["alerted_dca"] = []
                    state["alerted_dca"].append(alert_key)
                    if len(state["alerted_dca"]) > 50:
                        state["alerted_dca"] = state["alerted_dca"][-20:]
        
        # Respect API Rate Limits
        time.sleep(CONFIG["api"]["request_delay"])

    # 3. SEND WEEKLY DIGEST (if it's the right day/time)
    if is_weekly_reminder_day and current_dca_coins:
        print(f"[*] Sending weekly digest ({len(current_dca_coins)} coins in DCA zones)")
        weekly_msg = format_weekly_digest(current_dca_coins)
        send_discord(WEBHOOK_INVESTMENT_WATCH, weekly_msg)
        state["last_weekly_reminder"] = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        owner_logs.append(f"📊 Weekly Digest Sent: {len(current_dca_coins)} active DCA zones")
    elif is_weekly_reminder_day:
        # Send even if no DCA zones (to show market is expensive)
        weekly_msg = format_weekly_digest([])
        send_discord(WEBHOOK_INVESTMENT_WATCH, weekly_msg)
        state["last_weekly_reminder"] = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        owner_logs.append("📊 Weekly Digest Sent: No active DCA zones")

    # 4. SEND OWNER HUB SUMMARY
    if owner_logs:
        owner_summary = (
            f"🤖 **DAILY MACRO SCAN COMPLETE**\n"
            f" {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(owner_logs)
        )
        send_discord(WEBHOOK_OWNER_HUB, owner_summary)

    save_state(state)
    print("[*] Scan complete. State saved.\n")

# ==============================================================================
# ENTRY POINT
# ==============================================================================
def main():
    print("=" * 60)
    print("  MACRO ACCUMULATION & VALUE BOT (v1.1)")
    print("  With Weekly Investment Digest")
    print("=" * 60)
    
    if not all([WEBHOOK_OWNER_HUB, WEBHOOK_INVESTMENT_WATCH, WEBHOOK_MACRO_DIGEST]):
        print("[!] WARNING: Missing one or more webhook secrets!")
    
    try:
        run_scan()
    except Exception as e:
        print(f"[!] Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()