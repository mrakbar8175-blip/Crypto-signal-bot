#!/usr/bin/env python3
"""
Simple Forex News Bot
Just posts financial news. That's it.
"""

import os
import requests
import feedparser
from datetime import datetime

# Discord webhook
WEBHOOK_URL = os.environ.get("FOREX_WEBHOOK")

# News sources (RSS feeds)
NEWS_SOURCES = [
    "https://www.forexfactory.com/calendar.php?feed=1",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.investing.com/rss/news.rss"
]

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

def format_news(news_items):
    """Format news for Discord"""
    if not news_items:
        return "📰 **FINANCIAL NEWS**\nNo news available at the moment."
    
    message = "📰 **FINANCIAL NEWS**\n"
    message += f" {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}\n"
    message += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, item in enumerate(news_items[:5], 1):  # Show top 5
        message += f"{i}. **{item['title']}**\n"
        message += f"   🔗 {item['link']}\n\n"
    
    return message[:1950]  # Discord limit

def main():
    print("Fetching financial news...")
    news = fetch_news()
    
    if news:
        message = format_news(news)
        print(message)
        send_to_discord(message)
    else:
        print("No news found")

if __name__ == "__main__":
    main()