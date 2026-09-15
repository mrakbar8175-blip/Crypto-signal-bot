#!/usr/bin/env python3
"""
Forex News & Macro Bot (v1.0)
================================
Scans breaking Forex news and economic data every 30 minutes.
Maps global macro events to specific currency pairs.

Features:
  - Breaking Forex News Scanner (ForexLive RSS)
  - Currency Pair Mapping (Tells users exactly what to watch)
  - High-Impact Keyword Filtering (CPI, NFP, Rate Decisions, etc.)
  - Clean 2-Channel Routing (Owner Hub, Forex News)

Data Source: ForexLive RSS Feed (Free, no API key required)
"""

import os
import json
import sys
import requests
import feedparser
from datetime import datetime, timezone, timedelta

# ==============================================================================
# CONFIGURATION
# ==============================================================================
# High-impact Forex keywords to filter for
HIGH_IMPACT_KEYWORDS = [
    "CPI", "Inflation", "NFP", "Non-Farm", "Employment", "Unemployment",
    "Rate Decision", "Interest Rate", "FOMC", "Fed", "ECB", "BoE", "BoJ", 
    "RBA", "BoC", "GDP", "PMI", "Retail Sales", "Trade Balance", "PPI"
]

# Map currencies to their major pairs
CURRENCY_PAIRS = {
    "USD": ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CAD", "AUD/USD"],
    "EUR": ["EUR/USD", "EUR/GBP", "EUR/JPY"],
    "GBP": ["GBP/USD", "EUR/GBP", "GBP/JPY"],
    "JPY": ["USD/JPY", "EUR/JPY", "GBP/JPY"],
    "CAD": ["USD/CAD", "CAD/JPY"],
    "AUD": ["AUD/USD", "AUD/JPY"],
    "CHF": ["USD/CHF", "EUR/CHF"]
}

# Map country/region names to currencies
COUNTRY_TO_CURRENCY = {
    "US": "USD", "USA": "USD", "United States": "USD", "Federal Reserve": "USD", "Fed": "USD",
    "EU": "EUR", "Europe": "EUR", "Eurozone": "EUR", "ECB": "EUR",
    "UK": "GBP", "Britain": "GBP", "United Kingdom": "GBP", "BoE": "GBP",
    "Japan": "JPY", "BoJ": "JPY",
    "Canada": "CAD", "BoC": "CAD",
    "Australia": "AUD", "RBA": "AUD",
    "Switzerland": "CHF", "SNB": "CHF"
}

CONFIG = {
    "rss_url": "https://www.forexlive.com/feed/",
    "max_age_hours": 2,  # Only process news from the last 2 hours
    "files": {
        "state_file": "forex_state.json"
    }
}

# Webhooks
WEBHOOK_OWNER_HUB = os.environ.get("WEBHOOK_OWNER_HUB")
WEBHOOK_FOREX_NEWS = os.environ.get("WEBHOOK_FOREX_NEWS")

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
    return {"processed_titles": []}

def save_state(state):
    filepath = CONFIG["files"]["state_file"]
    tmp = filepath + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, filepath)

# ==============================================================================
# NEWS FETCHING & ANALYSIS
# ==============================================================================
def fetch_forex_news():
    """Fetches breaking news from ForexLive RSS."""
    print("[*] Fetching ForexLive RSS feed...")
    try:
        feed = feedparser.parse(CONFIG["rss_url"])
        news_items = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=CONFIG["max_age_hours"])
        
        for entry in feed.entries:
            # Parse published time (ForexLive uses standard RSS dates)
            published = entry.get("published_parsed")
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff_time:
                    continue
            
            news_items.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", "")
            })
        
        print(f"[✓] Fetched {len(news_items)} recent news items")
        return news_items
    except Exception as e:
        print(f"[!] Error fetching RSS: {e}")
        return []

def is_high_impact(title):
    """Checks if the news title contains high-impact Forex keywords."""
    title_upper = title.upper()
    return any(keyword.upper() in title_upper for keyword in HIGH_IMPACT_KEYWORDS)

def map_to_pairs(title):
    """Determines which currency pairs are affected by the news."""
    affected_currencies = set()
    title_upper = title.upper()
    
    # Check country/central bank mappings
    for key, currency in COUNTRY_TO_CURRENCY.items():
        if key.upper() in title_upper:
            affected_currencies.add(currency)
            
    # If no specific country found, default to USD (most Forex news affects USD)
    if not affected_currencies:
        affected_currencies.add("USD")
        
    # Collect all unique pairs
    affected_pairs = set()
    for currency in affected_currencies:
        affected_pairs.update(CURRENCY_PAIRS.get(currency, []))
        
    return list(affected_pairs)

# ==============================================================================
# FORMATTING & ROUTING
# ==============================================================================
def format_owner_log(title, pairs):
    """Verbose log for the Owner Hub."""
    pairs_str = ", ".join(pairs)
    return (
        f"📰 **NEWS LOG**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 {title}\n"
        f"🎯 Pairs: `{pairs_str}`\n"
    )

def format_clean_alert(title, pairs):
    """Clean, actionable alert for the Forex News channel."""
    pairs_str = ", ".join([f"`{p}`" for p in pairs])
    
    return (
        f"🚨 **BREAKING FOREX NEWS**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 **{title}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👁️ **Watch These Pairs:**\n{pairs_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ *Volatility expected. Manage your risk accordingly.*"
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
    print(f"\n[*] Starting Forex News Scan at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    
    state = load_state()
    processed_titles = state.get("processed_titles", [])
    
    # Keep the memory list from growing infinitely (keep last 200)
    if len(processed_titles) > 200:
        processed_titles = processed_titles[-200:]
        
    owner_logs = []
    alerts_sent = 0

    # 1. FETCH NEWS
    news_items = fetch_forex_news()
    
    # 2. PROCESS NEWS
    for item in news_items:
        title = item["title"]
        
        # Skip if already processed
        if title in processed_titles:
            continue
            
        # Check if high impact
        if is_high_impact(title):
            pairs = map_to_pairs(title)
            
            # Log to Owner Hub
            owner_logs.append(format_owner_log(title, pairs))
            
            # Send to Forex News Channel
            msg = format_clean_alert(title, pairs)
            send_discord(WEBHOOK_FOREX_NEWS, msg)
            
            # Mark as processed
            processed_titles.append(title)
            alerts_sent += 1
            print(f"[!] Alert Sent: {title}")

    # 3. SEND OWNER HUB SUMMARY
    if owner_logs:
        owner_summary = (
            f"🤖 **FOREX NEWS SCAN COMPLETE**\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(owner_logs)
        )
        send_discord(WEBHOOK_OWNER_HUB, owner_summary)
    else:
        # Send a quiet "all clear" to owner hub so you know it ran
        send_discord(WEBHOOK_OWNER_HUB, f"✅ **Forex Scan Complete**\n {datetime.now(timezone.utc).strftime('%H:%M UTC')}\nNo new high-impact news.")

    # 4. SAVE STATE
    state["processed_titles"] = processed_titles
    save_state(state)
    
    print(f"[*] Scan complete. {alerts_sent} alerts sent. State saved.\n")

# ==============================================================================
# ENTRY POINT
# ==============================================================================
def main():
    print("=" * 60)
    print("  FOREX NEWS & MACRO BOT (v1.0)")
    print("=" * 60)
    
    if not all([WEBHOOK_OWNER_HUB, WEBHOOK_FOREX_NEWS]):
        print("[!] WARNING: Missing webhook secrets!")
    
    try:
        run_scan()
    except Exception as e:
        print(f"[!] Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()