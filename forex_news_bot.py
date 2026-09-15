#!/usr/bin/env python3
"""
Comprehensive Forex News & Events Bot
Posts financial news + ALL market-moving events (High & Medium impact)
"""

import os
import requests
import feedparser
import json
from datetime import datetime, timedelta

# Discord webhook
WEBHOOK_URL = os.environ.get("FOREX_WEBHOOK")
STATE_FILE = "posted_news.json"

# News sources
NEWS_SOURCES = [
    "https://www.investing.com/rss/news.rss",
    "https://feeds.reuters.com/reuters/businessNews"
]
ECONOMIC_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Major currencies that move the Forex market
MAJOR_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"posted_titles": []}
    return {"posted_titles": []}

def save_state(state):
    with open(STATE_FILE + ".tmp", 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(STATE_FILE + ".tmp", STATE_FILE)

def send_to_discord(message):
    if not WEBHOOK_URL:
        print("No webhook configured")
        return False
    try:
        resp = requests.post(WEBHOOK_URL, json={"content": message})
        return resp.status_code in [200, 204]
    except Exception as e:
        print(f"Error: {e}")
        return False

def fetch_news():
    news_items = []
    for feed_url in NEWS_SOURCES:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                news_items.append({"title": entry.title, "link": entry.link})
        except:
            pass
    return news_items

def fetch_economic_events():
    events = []
    try:
        resp = requests.get(ECONOMIC_CALENDAR_URL, timeout=10)
        data = resp.json()
        today = datetime.now().date()
        week_end = today + timedelta(days=7)

        for event in data:
            country = event.get("country", "")
            impact = event.get("impact", "")
            
            # Filter: Major currencies ONLY, and High/Medium impact ONLY
            if country not in MAJOR_CURRENCIES:
                continue
            if impact not in ["High", "Medium", "3", "2", "🔴", "🟠"]:
                continue

            # Parse date
            try:
                date_str = event.get("date", "")
                event_date = datetime.strptime(date_str, "%b %d, %Y %H:%M")
                
                if today <= event_date.date() <= week_end:
                    # Determine impact emoji
                    impact_emoji = "🔴" if impact in ["High", "3", "🔴"] else ""
                    
                    events.append({
                        "date_obj": event_date,
                        "day": event_date.strftime("%A"),
                        "time": event_date.strftime("%H:%M"),
                        "country": country,
                        "event": event.get("title", ""),
                        "impact_emoji": impact_emoji
                    })
            except:
                pass

        # Sort by date, prioritize High impact (🔴) over Medium (🟠)
        events.sort(key=lambda x: (x["date_obj"], 0 if x["impact_emoji"] == "🔴" else 1))
        
        # Limit to top 15 events to prevent Discord character limit issues
        return events[:15]
    except Exception as e:
        print(f"Error fetching events: {e}")
        return []

def get_country_flag(code):
    flags = {"USD": "", "EUR": "", "GBP": "", "JPY": "", "AUD": "", "CAD": "", "CHF": "", "NZD": ""}
    return flags.get(code, "")

def format_message(news_items, economic_events, state):
    today = datetime.now().strftime("%A, %B %d")
    message = f" **FINANCIAL NEWS & FOREX EVENTS**\n{today}\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # --- SECTION 1: FOREX EVENTS (Grouped by Day) ---
    if economic_events:
        message += " **THIS WEEK'S MARKET EVENTS**\n"
        current_day = ""
        for event in economic_events:
            # Print Day Header if it changes
            if event["day"] != current_day:
                current_day = event["day"]
                message += f"\n **{current_day}**\n"
            
            flag = get_country_flag(event["country"])
            # Format: 🔴 12:30 - US Core CPI
            message += f"{event['impact_emoji']} {event['time']} - {flag} {event['event']}\n"
        message += "\n"
    else:
        message += " **THIS WEEK'S EVENTS**\n*No major market-moving events found*\n\n"

    # --- SECTION 2: LATEST NEWS ---
    if news_items:
        message += " **LATEST FINANCIAL NEWS**\n"
        count = 0
        for item in news_items[:6]:
            if item["title"] not in state["posted_titles"]:
                # Clickable link format
                message += f"{count + 1}. **{item['title']}** [Read More]({item['link']})\n\n"
                state["posted_titles"].append(item["title"])
                count += 1
                if count >= 4: # Limit news to 4 items to save space for events
                    break
        if count == 0:
            message += "*No new news since last update*\n\n"

    message += "━━━━━━━━━━━━━━━━━━━━━━━━\n💡 *Stay informed, trade safe*"
    
    # Hard limit for Discord
    return message[:1900], state

def main():
    print("Fetching data...")
    state = load_state()
    news = fetch_news()
    events = fetch_economic_events()
    
    print(f"Found {len(news)} news and {len(events)} forex events")
    
    new_news = sum(1 for item in news if item["title"] not in state["posted_titles"])
    
    if new_news == 0 and not events:
        print("Nothing new to post.")
        return

    message, updated_state = format_message(news, events, state)
    
    if send_to_discord(message):
        updated_state["posted_titles"] = updated_state["posted_titles"][-100:]
        save_state(updated_state)
        print("✓ Posted successfully")

if __name__ == "__main__":
    main()