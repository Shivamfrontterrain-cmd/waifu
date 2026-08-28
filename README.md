# Telegram Waifu Database & Media Scraper 🌸

An automated Telegram MTProto userbot scraper designed to extract waifu/character names, anime origins, rarities, extra metadata, and high-resolution artwork from all channels located inside a specified Telegram Chat Folder (e.g., `databases`).

---

## 📁 Project Structure

```
waifuscraper/
├── .env.example          # Environment variables template
├── .env                  # Your actual Telegram credentials (create this)
├── config.py             # Configuration loader
├── parser.py             # Smart character metadata parser
├── database.py           # SQLite database manager & JSON/CSV exporter
├── scraper.py            # Main Telegram scraper & media downloader
├── inspect_sample.py     # Test script to inspect channel messages & verify parsing
├── requirements.txt      # Python dependencies
├── downloads/            # Downloaded character images
│   └── images/
└── database/             # SQLite DB, JSON, and CSV outputs
    ├── waifus.db
    ├── waifus.json
    └── waifus.csv
```

---

## 🚀 Quick Setup Guide

### 1. Get Telegram API Credentials
1. Go to [https://my.telegram.org](https://my.telegram.org) and log in with your Telegram account.
2. Click **API development tools**.
3. Create a new application (you can name it anything, e.g., `WaifuScraper`).
4. Copy your `api_id` and `api_hash`.

---

### 2. Configure Environment (`.env`)
Create a `.env` file in this directory (or copy `.env.example` to `.env`):

```ini
TG_API_ID=12345678
TG_API_HASH=0123456789abcdef0123456789abcdef
TG_PHONE=+1234567890
FOLDER_NAME=databases

DOWNLOAD_IMAGES=true
IMAGE_DIR=downloads/images
DATABASE_DIR=database
DB_FILE=database/waifus.db
EXPORT_JSON=database/waifus.json
EXPORT_CSV=database/waifus.csv
```

---

### 3. Test & Inspect Your Channels
Before running a full scrape, run the inspector to verify your folder detection and preview how the parser extracts your waifu names:

```bash
py inspect_sample.py
```
> *On the first run, Telegram will ask for your phone number and send a login code to your Telegram app. Enter the code and your 2FA password (if enabled). It will save your session in `waifu_session.session` for future runs.*

---

### 4. Run the Full Scraper
Once you confirm the sample output looks great:

```bash
py scraper.py
```

---

## 📊 Features & Database Schema

### What gets extracted:
- **`Character Name`**: e.g., *Rem*, *Mikasa Ackerman*, *Marin Kitagawa*
- **`Anime / Source`**: e.g., *Re:Zero*, *Attack on Titan*, *My Dress-Up Darling*
- **`Rarity / Tier`**: e.g., *Legendary*, *⭐⭐⭐⭐⭐*, *Rare*
- **`Character ID`**: Bot or card identifier (if present)
- **`Event / Tag`**: Special seasonal/event tags
- **`Artwork / Photo`**: Saved to `downloads/images/<channel_name>/<character_name>_<msg_id>.jpg`
- **`Raw Caption`**: Full unmodified Telegram post text

### Output Files:
1. **SQLite Database**: `database/waifus.db` (Indexed for fast querying by character name or anime).
2. **JSON Export**: `database/waifus.json` (Ideal for feeding into your Telegram waifu bot).
3. **CSV Export**: `database/waifus.csv` (Easy viewing in Excel / Google Sheets).

### Resumable & Safe:
- **Duplicate Prevention**: Keeps track of scraped message IDs so you can stop and resume anytime without duplicate downloads.
- **Flood Control**: Automatically sleeps and retries if Telegram's rate-limiting (`FloodWaitError`) is encountered.
