#!/usr/bin/env python3
"""
Forex News Bot
- Shows ONLY today's economic events
- Makes entire news headline clickable
- No duplicates
"""

import os
import requests
import feedparser
import json
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get("FOREX_WEBHOOK")
STATE_FILE = "posted_news.json"

NEWS_SOURCES = [
    "https://www.investing.com/rss/news.rss",
    "https://feeds.reuters.com/reuters/businessNews"
]

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
        return False
    try:
        resp = requests.post(WEBHOOK_URL, json={"content": message})
        return resp.status_code in [200, 204]
    except:
        return False

def fetch_news():
    news_items = []
    for feed_url in NEWS_SOURCES:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:8]:
                news_items.append({
                    "title": entry.title,
                    "link": entry.link
                })
        except:
            pass
    return news_items

def fetch_today_events():
    """Fetch ONLY today's economic events"""
    events = []
    today = datetime.now().date()
    
    try:
        print("[*] Fetching economic calendar...")
        resp = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10)
        data = resp.json()
        
        for event in data:
            country = event.get("country", "")
            impact = event.get("impact", "")
            title = event.get("title", "")
            date_str = event.get("date", "")
            
            # Only major currencies
            if country not in ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]:
                continue
            
            # Only High/Medium impact
            if impact not in ["High", "Medium", "3", "2"]:
                continue
            
            try:
                # Parse the date
                event_date = datetime.strptime(date_str, "%b %d, %Y %H:%M")
                
                # ONLY today's events
                if event_date.date() == today:
                    impact_emoji = "🔴" if impact in ["High", "3"] else ""
                    events.append({
                        "time": event_date.strftime("%H:%M"),
                        "country": country,
                        "event": title,
                        "impact": impact_emoji,
                        "datetime": event_date
                    })
            except:
                continue
        
        # Sort by time
        events.sort(key=lambda x: x["datetime"])
        print(f"[✓] Found {len(events)} events for today")
        
    except Exception as e:
        print(f"[!] Error: {e}")
    
    return events

def get_flag(code):
    flags = {
        "USD": "",
        "EUR": "",
        "GBP": "",
        "JPY": "",
        "AUD": "",
        "CAD": "",
        "CHF": "",
        "NZD": ""
    }
    return flags.get(code, "")

def format_message(news_items, economic_events, state):
    today = datetime.now().strftime("%A, %B %d")
    
    message = f" **FOREX NEWS & EVENTS**\n"
    message += f"{today}\n"
    message += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # TODAY'S EVENTS
    if economic_events:
        message += f" **TODAY'S EVENTS ({len(economic_events)})**\n"
        for event in economic_events:
            flag = get_flag(event["country"])
            message += f"{event['impact']} {event['time']} - {flag} {event['event']}\n"
        message += "\n"
    else:
        message += " **TODAY'S EVENTS**\n*No major events today*\n\n"
    
    # LATEST NEWS (entire line clickable)
    if news_items:
        message += " **LATEST NEWS**\n"
        count = 0
        for item in news_items[:8]:
            if item["title"] not in state.get("posted_titles", []):
                # Make ENTIRE title clickable
                message += f"• [{item['title']}]({item['link']})\n\n"
                state.setdefault("posted_titles", []).append(item["title"])
                count += 1
                if count >= 6:
                    break
        
        if count == 0:
            message += "*No new news*\n\n"
    
    message += "━━━━━━━━━━━━━━━━━━━━━━━━\n💡 *Stay informed*"
    
    return message[:1900], state

def main():
    print("Starting Forex bot...")
    state = load_state()
    
    news = fetch_news()
    events = fetch_today_events()
    
    print(f"News: {len(news)}, Today's Events: {len(events)}")
    
    if not news and not events:
        print("Nothing to post")
        return
    
    message, updated_state = format_message(news, events, state)
    
    if send_to_discord(message):
        updated_state["posted_titles"] = updated_state.get("posted_titles", [])[-50:]
        save_state(updated_state)
        print("✓ Posted to Discord")
    else:
        print("✗ Failed")

if __name__ == "__main__":
    main()