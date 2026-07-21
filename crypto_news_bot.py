import requests
import os
import json
import feedparser
from datetime import datetime

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("CRYPTO_NEWS_WEBHOOK", "PASTE_YOUR_WEBHOOK_HERE")
STATE_FILE = "sent_crypto_news.json"

# RSS Feeds from major crypto news sites (100% public, no keys needed)
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed"
]

def load_sent_news():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_sent_news(sent_set):
    recent_urls = list(sent_set)[-100:] # Keep last 100 to prevent file bloat
    with open(STATE_FILE, 'w') as f:
        json.dump(recent_urls, f)

def get_trending_coins():
    """Fetches top 3 trending coins from CoinGecko (Free, no API key)."""
    url = "https://api.coingecko.com/api/v3/search/trending"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        trending = []
        for coin in data.get('coins', [])[:3]:
            name = coin['item']['name']
            symbol = coin['item']['symbol']
            market_cap_rank = coin['item'].get('market_cap_rank', 'N/A')
            trending.append(f"**{name}** ({symbol}) - Rank #{market_cap_rank}")
        return trending
    except Exception as e:
        print(f"⚠️ Error fetching trending coins: {e}")
        return ["*Could not fetch trending coins right now.*"]

def get_crypto_news():
    """Fetches latest headlines from RSS feeds."""
    print("📰 Fetching latest crypto news...")
    news_items = []
    
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            # Get the top 3 entries from each feed
            for entry in feed.entries[:3]:
                title = entry.get('title', '')
                link = entry.get('link', '')
                published = entry.get('published', '')
                
                # Create a unique ID to prevent duplicates
                news_id = link
                
                news_items.append({
                    'id': news_id,
                    'title': title,
                    'link': link,
                    'published': published
                })
        except Exception as e:
            print(f"⚠️ Error parsing RSS feed {feed_url}: {e}")
            continue
            
    return news_items

def send_to_discord(news_items, trending_coins):
    if not news_items:
        print(" No new news found.")
        return
        
    description = ""
    
    # Section 1: Breaking News
    description += "📰 **BREAKING CRYPTO NEWS**\n"
    for item in news_items[:5]: # Show top 5 unique headlines
        description += f"🔹 **[{item['title']}]({item['link']})**\n"
    description += "\n"
    
    # Section 2: Trending Coins
    description += "🔥 **TRENDING COINS RIGHT NOW**\n"
    for coin in trending_coins:
        description += f" {coin}\n"
        
    description += "\n💡 *Stay ahead of the market with Signal.*"
    
    payload = {
        "username": "Crypto News Bot",
        "embeds": [{
            "title": "🚨 DAILY CRYPTO SIGNAL & NEWS 🚨",
            "description": description,
            "color": 15105570, # Orange
            "footer": {"text": f"Updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"}
        }]
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        print("✅ News sent to Discord!")
    except Exception as e:
        print(f"❌ Failed to send to Discord: {e}")

if __name__ == "__main__":
    already_sent = load_sent_news()
    
    # 1. Get News
    all_news = get_crypto_news()
    new_news = [n for n in all_news if n['id'] not in already_sent]
    
    # 2. Get Trending Coins
    trending = get_trending_coins()
    
    if new_news:
        print(f"🎉 Found {len(new_news)} NEW news articles!")
        send_to_discord(new_news, trending)
        
        # Save to memory
        for n in new_news:
            already_sent.add(n['id'])
        save_sent_news(already_sent)
        print("💾 State saved.")
    else:
        print("No new news found (already posted recent headlines).")