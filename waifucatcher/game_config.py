import os
from pathlib import Path
from dotenv import load_dotenv

# Project Directories
CATCHER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CATCHER_DIR.parent

# Load .env from project root
load_dotenv(PROJECT_ROOT / ".env")

# Telegram Bot Credentials
BOT_TOKEN = os.getenv("CATCHER_BOT_TOKEN", "").strip() or os.getenv("BOT_TOKEN", "").strip()

# Database Paths
DATABASE_DIR = PROJECT_ROOT / "database"
DATABASE_DIR.mkdir(parents=True, exist_ok=True)

# Shared Character Catalog (192,000+ characters)
CHARACTER_DB_PATH = DATABASE_DIR / "waifus.db"

# Dedicated Game State Database (Player inventories, harems, economy, spawns)
GAME_DB_PATH = DATABASE_DIR / "game.db"

# Game Economy and Spawn Settings
DEFAULT_SPAWN_INTERVAL = int(os.getenv("SPAWN_INTERVAL", "30").strip())  # Messages before a wild waifu spawns
SPAWN_TIMEOUT_SECONDS = int(os.getenv("SPAWN_TIMEOUT", "300").strip())  # 5 minutes before unclaimed spawn despawns
DAILY_COIN_REWARD = 500  # Daily login coins
ROLL_COST_COINS = 100    # Cost for 1 gacha summon roll
CLAIM_REWARD_COINS = 75  # Bonus coins awarded to whoever catches a wild spawn first
INITIAL_BALANCE = 250    # Starting coins for new players

# Rarity Gacha Multipliers and Probabilities
RARITIES = {
    "Common": {"weight": 55, "color": "⚪", "sell_price": 25},
    "Rare": {"weight": 25, "color": "🟢", "sell_price": 60},
    "Super Rare": {"weight": 12, "color": "🔵", "sell_price": 150},
    "Epic": {"weight": 5, "color": "🟣", "sell_price": 350},
    "Legendary": {"weight": 2.5, "color": "🟡", "sell_price": 800},
    "Mythical": {"weight": 0.5, "color": "🔴", "sell_price": 2000},
    "Event": {"weight": 0, "color": "✨", "sell_price": 1000},
}


def validate_catcher_config():
    """Validates required bot configuration."""
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN (or CATCHER_BOT_TOKEN) is missing in .env")
    if not CHARACTER_DB_PATH.exists():
        errors.append(f"Character database not found at {CHARACTER_DB_PATH}. Please ensure waifus.db is present.")
    return errors
