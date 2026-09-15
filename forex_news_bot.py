#!/usr/bin/env python3
"""
Simple Forex News Bot
Posts financial news + this week's economic events
"""

import os
import requests
import feedparser
from datetime import datetime, timedelta

# Discord webhook
WEBHOOK_URL = os.environ.get("FOREX_WEBHOOK")

# News sources (RSS feeds)
NEWS_SOURCES = [
    "https://www.investing.com/rss/news.rss",
    "https://feeds.reuters.com/reuters/businessNews"
]

# Economic calendar API (ForexFactory)
ECONOMIC_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

def send_to_discord(message):
    """Send message to Discord"""
    if not WEBHOOK_URL:
        print("No webhook configured")
        return
    
    try:
        requests.post(WEBHOOK_URL, json={"content": message})
        print("✓ News sent to Discord")
    except Exception as e:
        print(f"Error: {e}")

def shorten_link(long_url):
    """Make links shorter and cleaner"""
    # Remove long parameters and keep only the base URL
    if "investing.com" in long_url:
        # Extract just the article slug
        parts = long_url.split("/news/")
        if len(parts) > 1:
            slug = parts[1].split("-")[0]  # Get first part before numbers
            return f"https://investing.com/news/{slug}"
    elif "reuters.com" in long_url:
        return "reuters.com/article"
    
    # Default: just show domain
    domain = long_url.split("//")[-1].split("/")[0]
    return domain

def fetch_news():
    """Fetch latest financial news"""
    news_items = []
    
    for feed_url in NEWS_SOURCES:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:3]:  # Get top 3 from each source
                news_items.append({
                    "title": entry.title,
                    "link": entry.link,
                    "published": entry.get("published", "N/A")
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
        
        # Filter for high impact events only
        for event in data:
            if event.get("impact") == "High" or event.get("impact") == "3":
                # Parse the date
                try:
                    date_str = event.get("date", "")
                    event_date = datetime.strptime(date_str, "%b %d, %Y %H:%M")
                    
                    # Only include events from today onwards this week
                    if event_date >= datetime.now() - timedelta(days=1):
                        events.append({
                            "date": event_date.strftime("%a %H:%M UTC"),
                            "country": event.get("country", ""),
                            "event": event.get("title", ""),
                            "impact": event.get("impact", "")
                        })
                except:
                    pass
        
        # Limit to top 5 events
        return events[:5]
    except Exception as e:
        print(f"Error fetching economic events: {e}")
        return []

def format_message(news_items, economic_events):
    """Format a clean message for Discord"""
    today = datetime.now().strftime("%A, %B %d, %Y")
    
    message = f"📰 **FINANCIAL NEWS & EVENTS**\n"
    message += f"{today}\n"
    message += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # Section 1: This Week's Economic Events
    if economic_events:
        message += "️ **THIS WEEK'S HIGH-IMPACT EVENTS**\n"
        for event in economic_events:
            flag = get_country_flag(event["country"])
            message += f"{flag} **{event['date']}** - {event['event']}\n"
        message += "\n"
    
    # Section 2: Latest News
    if news_items:
        message += " **LATEST NEWS**\n"
        for i, item in enumerate(news_items[:5], 1):
            short_link = shorten_link(item["link"])
            message += f"{i}. **{item['title']}**\n"
            message += f"    {short_link}\n\n"
    
    message += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    message += "💡 *Stay informed, trade safe*"
    
    return message[:1950]  # Discord limit

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
        "INR": ""
    }
    return flags.get(country_code, "")

def main():
    print("Fetching financial news and economic events...")
    
    # Fetch both news and events
    news = fetch_news()
    events = fetch_economic_events()
    
    if news or events:
        message = format_message(news, events)
        print(message)
        send_to_discord(message)
    else:
        print("No news or events found")

if __name__ == "__main__":
    main()