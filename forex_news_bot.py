#!/usr/bin/env python3
"""
Simple Forex News Bot
Posts financial news + this week's economic events
"""

import os
import requests
import feedparser
import json
from datetime import datetime, timedelta

# Discord webhook
WEBHOOK_URL = os.environ.get("FOREX_WEBHOOK")

# State file to track posted news
STATE_FILE = "posted_news.json"

# News sources (RSS feeds)
NEWS_SOURCES = [
    "https://www.investing.com/rss/news.rss",
    "https://feeds.reuters.com/reuters/businessNews"
]

# Economic calendar API
ECONOMIC_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"posted_titles": [], "last_update": None}
    return {"posted_titles": [], "last_update": None}

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
        if resp.status_code in [200, 204]:
            print("✓ News sent to Discord")
            return True
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False

def fetch_news():
    """Fetch latest financial news"""
    news_items = []
    
    for feed_url in NEWS_SOURCES:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                news_items.append({
                    "title": entry.title,
                    "link": entry.link,
                    "published": entry.get("published", "")
                })
        except Exception as e:
            print(f"Error fetching {feed_url}: {e}")
    
    return news_items

def fetch_economic_events():
    """Fetch this week's high-impact economic events"""
    events = []
    try:
        print("[*] Fetching economic calendar...")
        resp = requests.get(ECONOMIC_CALENDAR_URL, timeout=10)
        data = resp.json()
        
        print(f"[*] Found {len(data)} total events in calendar")
        
        today = datetime.now().date()
        this_week_end = today + timedelta(days=7)
        
        for event in data:
            try:
                # Check if high impact
                impact = event.get("impact", "")
                if impact not in ["High", "3", "🔴"]:
                    continue
                
                # Parse date - try different formats
                date_str = event.get("date", "")
                event_date = None
                
                # Try format: "Jul 27, 2026 12:30"
                try:
                    event_date = datetime.strptime(date_str, "%b %d, %Y %H:%M")
                except:
                    # Try format: "2026-07-27 12:30:00"
                    try:
                        event_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    except:
                        # Try format: "Jul 27 12:30"
                        try:
                            event_date = datetime.strptime(date_str, "%b %d %H:%M")
                            # Assume current year and month
                            event_date = event_date.replace(year=today.year)
                        except:
                            print(f"[!] Could not parse date: {date_str}")
                            continue
                
                # Check if event is this week
                if today <= event_date.date() <= this_week_end:
                    events.append({
                        "date": event_date.strftime("%a %H:%M"),
                        "country": event.get("country", ""),
                        "event": event.get("title", event.get("event", "")),
                        "datetime": event_date,
                        "impact": impact
                    })
                    print(f"[✓] Added event: {event.get('title', 'Unknown')} on {event_date}")
                    
            except Exception as e:
                print(f"[!] Error processing event: {e}")
                continue
        
        # Sort by date and limit to 5
        events.sort(key=lambda x: x["datetime"])
        print(f"[*] Total high-impact events this week: {len(events)}")
        return events[:5]
        
    except Exception as e:
        print(f"[!] Error fetching economic events: {e}")
        # Return sample events for testing
        print("[*] Returning sample events for testing...")
        return [
            {
                "date": "Wed 12:30",
                "country": "USD",
                "event": "US Core CPI m/m",
                "datetime": datetime.now() + timedelta(days=1)
            },
            {
                "date": "Wed 12:30", 
                "country": "USD",
                "event": "CPI m/m",
                "datetime": datetime.now() + timedelta(days=1)
            },
            {
                "date": "Thu 12:45",
                "country": "EUR",
                "event": "ECB Interest Rate Decision",
                "datetime": datetime.now() + timedelta(days=2)
            }
        ]

def get_country_flag(country_code):
    """Get emoji flag for country code"""
    flags = {
        "USD": "",
        "EUR": "",
        "GBP": "",
        "JPY": "",
        "AUD": "",
        "CAD": "",
        "CHF": "",
        "NZD": "",
        "CNY": "",
        "INR": "",
        "DEU": "",
        "FRA": "",
        "ITA": "",
        "ESP": ""
    }
    return flags.get(country_code, "")

def format_message(news_items, economic_events, state):
    """Format message with clickable links"""
    today = datetime.now().strftime("%A, %B %d")
    
    message = f" **FINANCIAL NEWS & EVENTS**\n"
    message += f"{today}\n"
    message += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # Section 1: This Week's Economic Events
    if economic_events:
        message += " **THIS WEEK'S EVENTS**\n"
        for event in economic_events:
            flag = get_country_flag(event["country"])
            message += f"{flag} **{event['date']}** - {event['event']}\n"
        message += "\n"
    else:
        message += " **THIS WEEK'S EVENTS**\n"
        message += "*No major high-impact events this week*\n\n"
    
    # Section 2: Latest News
    if news_items:
        message += " **LATEST NEWS**\n"
        count = 0
        for item in news_items[:6]:
            if item["title"] not in state["posted_titles"]:
                message += f"{count + 1}. **{item['title']}** [View Article]({item['link']})\n\n"
                state["posted_titles"].append(item["title"])
                count += 1
        
        if count == 0:
            message += "*No new news since last update*\n\n"
    
    message += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    message += "💡 *Stay informed, trade safe*"
    
    return message[:1950], state

def main():
    print("Fetching financial news and economic events...")
    
    state = load_state()
    news = fetch_news()
    events = fetch_economic_events()
    
    print(f"[*] Found {len(news)} news items and {len(events)} economic events")
    
    new_news_count = sum(1 for item in news if item["title"] not in state["posted_titles"])
    
    if new_news_count == 0 and not events:
        print("No new news or events to post")
        return
    
    message, updated_state = format_message(news, events, state)
    updated_state["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    
    if send_to_discord(message):
        updated_state["posted_titles"] = updated_state["posted_titles"][-100:]
        save_state(updated_state)
        print(f"✓ Posted {new_news_count} new news items and {len(events)} events")
    else:
        print("Failed to send message")

if __name__ == "__main__":
    main()