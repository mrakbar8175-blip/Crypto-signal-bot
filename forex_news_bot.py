#!/usr/bin/env python3
"""
Simple Forex News Bot
Posts financial news + this week's economic events
NO DUPLICATES + CLEAN LINKS
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

# Economic calendar API (ForexFactory)
ECONOMIC_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

def load_state():
    """Load previously posted news IDs"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"posted_titles": [], "last_update": None}
    return {"posted_titles": [], "last_update": None}

def save_state(state):
    """Save posted news IDs"""
    with open(STATE_FILE + ".tmp", 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(STATE_FILE + ".tmp", STATE_FILE)

def send_to_discord(message):
    """Send message to Discord"""
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

def clean_link(long_url):
    """Make links short and clean"""
    try:
        # Remove tracking parameters
        url = long_url.split("?")[0]
        
        if "investing.com" in url:
            # Extract clean path
            if "/news/" in url:
                parts = url.split("/news/")
                if len(parts) > 1:
                    # Get the category and slug, remove ID numbers at end
                    path = parts[1].rsplit("-", 1)[0] if "-" in parts[1] else parts[1]
                    return f"investing.com/news/{path}"
            return "investing.com"
        
        elif "reuters.com" in url:
            return "reuters.com/article"
        
        # Default: show just domain
        domain = url.split("//")[-1].split("/")[0].replace("www.", "")
        return domain
    except:
        return "link"

def fetch_news():
    """Fetch latest financial news"""
    news_items = []
    
    for feed_url in NEWS_SOURCES:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:  # Get top 5 from each source
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
        resp = requests.get(ECONOMIC_CALENDAR_URL, timeout=10)
        data = resp.json()
        
        today = datetime.now().date()
        
        # Filter for high impact events this week
        for event in data:
            if event.get("impact") in ["High", "3"]:
                try:
                    date_str = event.get("date", "")
                    event_date = datetime.strptime(date_str, "%b %d, %Y %H:%M")
                    
                    # Only future events or today's events
                    if event_date.date() >= today:
                        events.append({
                            "date": event_date.strftime("%a %H:%M"),
                            "country": event.get("country", ""),
                            "event": event.get("title", ""),
                            "datetime": event_date
                        })
                except:
                    pass
        
        # Sort by date and limit to 5
        events.sort(key=lambda x: x["datetime"])
        return events[:5]
    except Exception as e:
        print(f"Error fetching economic events: {e}")
        return []

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
        "FRA": ""
    }
    return flags.get(country_code, "")

def format_message(news_items, economic_events, state):
    """Format a clean message for Discord"""
    today = datetime.now().strftime("%A, %B %d")
    
    message = f"📰 **FINANCIAL NEWS & EVENTS**\n"
    message += f"{today}\n"
    message += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # Section 1: This Week's Economic Events
    if economic_events:
        message += " **THIS WEEK'S EVENTS**\n"
        for event in economic_events:
            flag = get_country_flag(event["country"])
            message += f"{flag} **{event['date']}** - {event['event']}\n"
        message += "\n"
    
    # Section 2: Latest News (only new ones)
    if news_items:
        message += "️ **LATEST NEWS**\n"
        count = 0
        for item in news_items[:6]:
            # Check if this news was already posted
            if item["title"] not in state["posted_titles"]:
                clean_url = clean_link(item["link"])
                message += f"{count + 1}. **{item['title']}**\n"
                message += f"   {clean_url}\n\n"
                state["posted_titles"].append(item["title"])
                count += 1
        
        if count == 0:
            message += "*No new news since last update*\n\n"
    
    message += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    message += "💡 *Stay informed, trade safe*"
    
    return message[:1950], state

def main():
    print("Fetching financial news and economic events...")
    
    # Load state
    state = load_state()
    
    # Fetch data
    news = fetch_news()
    events = fetch_economic_events()
    
    # Check if we have new content
    new_news_count = sum(1 for item in news if item["title"] not in state["posted_titles"])
    
    if new_news_count == 0 and not events:
        print("No new news or events to post")
        return
    
    # Format message
    message, updated_state = format_message(news, events, state)
    
    # Update timestamp
    updated_state["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    
    # Send to Discord
    if send_to_discord(message):
        # Keep only last 100 posted titles to prevent file bloat
        updated_state["posted_titles"] = updated_state["posted_titles"][-100:]
        save_state(updated_state)
        print(f"✓ Posted {new_news_count} new news items")
    else:
        print("Failed to send message")

if __name__ == "__main__":
    main()