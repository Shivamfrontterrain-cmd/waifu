import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Telegram Credentials
API_ID_RAW = os.getenv("TG_API_ID", "").strip()
API_ID = int(API_ID_RAW) if API_ID_RAW.isdigit() else None
API_HASH = os.getenv("TG_API_HASH", "").strip()
PHONE = os.getenv("TG_PHONE", "").strip()
SESSION_NAME = os.getenv("SESSION_NAME", "waifu_session").strip()

# Target Folder
FOLDER_NAME = os.getenv("FOLDER_NAME", "databases").strip()

# Settings
DOWNLOAD_IMAGES = os.getenv("DOWNLOAD_IMAGES", "true").strip().lower() in ("true", "1", "yes")
CONCURRENT_DOWNLOADS = int(os.getenv("CONCURRENT_DOWNLOADS", "8").strip()) if os.getenv("CONCURRENT_DOWNLOADS", "8").strip().isdigit() else 8
IMAGE_DIR = BASE_DIR / os.getenv("IMAGE_DIR", "downloads/images")
DATABASE_DIR = BASE_DIR / os.getenv("DATABASE_DIR", "database")
DB_PATH = BASE_DIR / os.getenv("DB_FILE", "database/waifus.db")
EXPORT_JSON_PATH = BASE_DIR / os.getenv("EXPORT_JSON", "database/waifus.json")
EXPORT_CSV_PATH = BASE_DIR / os.getenv("EXPORT_CSV", "database/waifus.csv")

# Ensure required directories exist
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_DIR.mkdir(parents=True, exist_ok=True)


def validate_config():
    """Validates if minimum required credentials are provided."""
    errors = []
    if not API_ID:
        errors.append("TG_API_ID is missing or invalid in .env")
    if not API_HASH:
        errors.append("TG_API_HASH is missing in .env")
    return errors
