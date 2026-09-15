#!/usr/bin/env python3
"""
Forex High-Impact News Bot
================================
Scans the ForexFactory calendar for "Red Folder" (High Impact) events 
and alerts the Discord server before they happen.

Features:
  - Filters ONLY High Impact events for major currencies (USD, EUR, GBP, JPY, etc.)
  - Alerts 2 hours before the event to give traders time to prepare.
  - Anti-spam memory (never alerts the same event twice).
  - 2-Channel Clean Routing (Owner Hub & Forex News).

Data Source: ForexFactory Public JSON Feed (Free, No API Key)
"""

import os
import json
import sys
import requests
from datetime import datetime, timezone, timedelta

# ==============================================================================
# CONFIGURATION
# ==============================================================================
CONFIG = {
    "major_currencies": ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"],
    "impact_filter": ["High"],  # Only alert on High impact (Red Folder)
    "alert_window_hours": 2,    # Alert traders X hours before the event
    "files": {
        "state_file": "forex_state.json"
    },
    "api": {
        # The famous free ForexFactory JSON feed
        "ff_calendar_url": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "request_timeout": 15
    }
}

# Webhooks for Routing
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
    return {"alerted_events": []}

def save_state(state):
    filepath = CONFIG["files"]["state_file"]
    tmp = filepath + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, filepath)

# ==============================================================================
# API & PARSING FUNCTIONS
# ==============================================================================
def fetch_forex_calendar():
    """Fetches the weekly ForexFactory calendar."""
    try:
        resp = requests.get(CONFIG["api"]["ff_calendar_url"], timeout=CONFIG["api"]["request_timeout"])
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[!] Error fetching Forex calendar: {e}")
        return []

def parse_ff_date(date_str):
    """
    Parses ForexFactory date format (e.g., 'Jul 27, 2026 12:30').
    Note: FF feed is typically in UTC.
    """
    try:
        # Remove any trailing 'UTC' or timezone text if present
        clean_date = date_str.replace("UTC", "").strip()
        dt = datetime.strptime(clean_date, "%b %d, %Y %H:%M")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def filter_high_impact_events(events):
    """Filters for High impact events on major currencies."""
    filtered = []
    for event in events:
        country = event.get("country", "")
        impact = event.get("impact", "")
        
        # Check if it's a major currency and high impact
        if country in CONFIG["major_currencies"] and impact in CONFIG["impact_filter"]:
            event_time = parse_ff_date(event.get("date", ""))
            if event_time:
                event["parsed_time"] = event_time
                filtered.append(event)
                
    return filtered

# ==============================================================================
# ALERT LOGIC & FORMATTING
# ==============================================================================
def get_event_emoji(title):
    """Assigns an emoji based on the type of economic event."""
    title_upper = title.upper()
    if "CPI" in title_upper or "INFLATION" in title_upper:
        return ""
    elif "FOMC" in title_upper or "RATE" in title_upper or "INTEREST" in title_upper:
        return "🏦"
    elif "NFP" in title_upper or "PAYROLL" in title_upper or "UNEMPLOYMENT" in title_upper or "JOBS" in title_upper:
        return ""
    elif "GDP" in title_upper:
        return ""
    elif "PMI" in title_upper or "MANUFACTURING" in title_upper:
        return "🏭"
    else:
        return "🔴"

def format_owner_log(event):
    """Verbose log for the Owner Hub."""
    return (
        f" **{event['country']} | {event['title']}**\n"
        f"  Time: {event['parsed_time'].strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"  Impact: {event['impact']}\n"
        f"  Forecast: {event.get('forecast', 'N/A')} | Previous: {event.get('previous', 'N/A')}\n"
    )

def format_clean_alert(event, hours_until):
    """Clean, urgent alert for the Forex News channel."""
    emoji = get_event_emoji(event["title"])
    time_str = event["parsed_time"].strftime("%H:%M UTC")
    date_str = event["parsed_time"].strftime("%b %d")
    
    forecast = event.get("forecast", "N/A")
    previous = event.get("previous", "N/A")
    
    return (
        f"{emoji} **HIGH IMPACT FOREX EVENT**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌍 **Country:** {event['country']}\n"
        f"📰 **Event:** {event['title']}\n"
        f"⏰ **Time:** {date_str} at {time_str} (in {hours_until}h)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Forecast:** `{forecast}`\n"
        f"📉 **Previous:** `{previous}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ **Action:** Expect high volatility in {event['country']} pairs. Tighten stops or step aside."
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
    alerted_events = state.get("alerted_events", [])
    
    # 1. Fetch and Filter
    print("[*] Fetching ForexFactory Calendar...")
    raw_events = fetch_forex_calendar()
    if not raw_events:
        print("[!] Failed to fetch calendar data.")
        return
        
    high_impact_events = filter_high_impact_events(raw_events)
    print(f"[✓] Found {len(high_impact_events)} High Impact events for major currencies.")
    
    owner_logs = []
    alerts_sent = 0
    now = datetime.now(timezone.utc)
    
    # 2. Analyze Events
    for event in high_impact_events:
        event_time = event["parsed_time"]
        time_until = event_time - now
        
        # Create a unique ID for the event to prevent spam
        event_id = f"{event['country']}_{event['title']}_{event_time.strftime('%Y%m%d%H%M')}"
        
        # Always log to Owner Hub
        owner_logs.append(format_owner_log(event))
        
        # Check if within alert window and not already alerted
        if timedelta(0) < time_until <= timedelta(hours=CONFIG["alert_window_hours"]):
            if event_id not in alerted_events:
                hours_until = round(time_until.total_seconds() / 3600, 1)
                msg = format_clean_alert(event, hours_until)
                
                print(f"[!] Alerting: {event['title']}")
                send_discord(WEBHOOK_FOREX_NEWS, msg)
                
                alerted_events.append(event_id)
                alerts_sent += 1
                
    # 3. Clean up old alerted events (keep list small)
    state["alerted_events"] = alerted_events[-100:]
    save_state(state)
    
    # 4. Send Owner Hub Summary
    if owner_logs:
        owner_summary = (
            f" **WEEKLY FOREX CALENDAR SCAN**\n"
            f" {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(owner_logs)
        )
        send_discord(WEBHOOK_OWNER_HUB, owner_summary)
        
    if alerts_sent > 0:
        print(f"\n[✓] Sent {alerts_sent} new Forex alerts!")
    else:
        print("\n[✓] No new high-impact events in the alert window.")
        
    print(f"[*] Scan complete. State saved.\n")

# ==============================================================================
# ENTRY POINT
# ==============================================================================
def main():
    print("=" * 60)
    print("  FOREX HIGH-IMPACT NEWS BOT")
    print("=" * 60)
    
    if not all([WEBHOOK_OWNER_HUB, WEBHOOK_FOREX_NEWS]):
        print("[!] WARNING: Missing one or more webhook secrets!")
    
    try:
        run_scan()
    except Exception as e:
        print(f"[!] Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()