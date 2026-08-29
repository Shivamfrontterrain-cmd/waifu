# Telegram Waifu Database & Identifier Bot 🌸⚡

An automated Telegram scraper and reverse-image identifier bot designed to scrape character names, anime series, rarities, and metadata directly from Telegram channel databases—**with zero image storage required**.

---

## 📁 Project Structure

```
waifuscraper/
├── .env.example          # Environment variables template
├── .env                  # Your actual Telegram credentials
├── config.py             # Configuration loader
├── parser.py             # Smart character metadata parser
├── database.py           # Lightweight database manager & JSON/CSV exporter
├── scraper.py            # High-speed text & metadata scraper (0 MB image downloads)
├── matcher.py            # Ultra-fast CPU-accelerated visual matching engine
├── bot.py                # Telegram Reverse-Image Waifu Identifier Bot
├── requirements.txt      # Python dependencies
└── database/             # SQLite DB, JSON, and CSV outputs
    ├── waifus.db
    ├── waifus.json
    └── waifus.csv
```

---

## 🚀 Quick Setup Guide

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure `.env`
Fill in your Telegram credentials in `.env`:
```ini
TG_API_ID=12345678
TG_API_HASH=your_api_hash
TG_PHONE=+1234567890
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
FOLDER_NAME=databases
```

### 3. Run the Scraper (Ultra-Fast Text Mode)
```bash
py scraper.py
```
- Scrapes all channel messages at lightning speed without downloading any media files.
- Automatically exports `database/waifus.json` and `database/waifus.csv`.

### 4. Run the Telegram Bot
```bash
py bot.py
```
- Forward or send any anime character photo to the bot.
- Instantly returns the Character Name (1-tap copyable), Anime, and Rarity in milliseconds!
