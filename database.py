import sqlite3
import json
import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from contextlib import contextmanager
from config import DB_PATH, EXPORT_JSON_PATH, EXPORT_CSV_PATH


class DatabaseManager:
    """Manages lightweight SQLite storage and exports with zero image file dependencies."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self):
        """Creates database tables with pure cloud Telegram identifiers and visual hashes."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Channels progress table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id INTEGER PRIMARY KEY,
                    channel_title TEXT,
                    channel_username TEXT,
                    last_msg_id INTEGER DEFAULT 0,
                    total_scraped INTEGER DEFAULT 0,
                    last_scraped_at TIMESTAMP
                )
            """)

            # Pure Cloud Characters table (0 MB image files required!)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS characters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_msg_id INTEGER,
                    channel_id INTEGER,
                    channel_title TEXT,
                    character_id TEXT,
                    name TEXT,
                    anime TEXT,
                    rarity TEXT,
                    event TEXT,
                    telegram_file_id TEXT,
                    telegram_file_unique_id TEXT,
                    image_phash TEXT,
                    image_dhash TEXT,
                    extra_info_json TEXT,
                    raw_text TEXT,
                    scraped_at TIMESTAMP,
                    UNIQUE(channel_id, telegram_msg_id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_char_name ON characters(name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_char_anime ON characters(anime)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_char_phash ON characters(image_phash)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_char_unique_id ON characters(telegram_file_unique_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_channel_msg ON characters(channel_id, telegram_msg_id)")
            conn.commit()

    def is_message_processed(self, channel_id: int, msg_id: int) -> bool:
        """Checks if a message from a given channel was already scraped."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM characters WHERE channel_id = ? AND telegram_msg_id = ?",
                (channel_id, msg_id)
            )
            return cursor.fetchone() is not None

    def save_character(
        self,
        telegram_msg_id: int,
        channel_id: int,
        channel_title: str,
        name: str,
        anime: str,
        rarity: Optional[str],
        character_id: Optional[str],
        event: Optional[str],
        telegram_file_id: Optional[str],
        telegram_file_unique_id: Optional[str],
        image_phash: Optional[str],
        image_dhash: Optional[str],
        extra_info: Dict[str, Any],
        raw_text: str
    ) -> int:
        """Inserts or updates a pure cloud character record."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO characters (
                    telegram_msg_id, channel_id, channel_title, character_id,
                    name, anime, rarity, event,
                    telegram_file_id, telegram_file_unique_id,
                    image_phash, image_dhash,
                    extra_info_json, raw_text, scraped_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, telegram_msg_id) DO UPDATE SET
                    character_id=excluded.character_id,
                    name=excluded.name,
                    anime=excluded.anime,
                    rarity=excluded.rarity,
                    event=excluded.event,
                    telegram_file_id=excluded.telegram_file_id,
                    telegram_file_unique_id=excluded.telegram_file_unique_id,
                    image_phash=COALESCE(excluded.image_phash, characters.image_phash),
                    image_dhash=COALESCE(excluded.image_dhash, characters.image_dhash),
                    extra_info_json=excluded.extra_info_json,
                    raw_text=excluded.raw_text,
                    scraped_at=excluded.scraped_at
            """, (
                telegram_msg_id, channel_id, channel_title, character_id,
                name, anime, rarity, event,
                telegram_file_id, telegram_file_unique_id,
                image_phash, image_dhash,
                json.dumps(extra_info, ensure_ascii=False),
                raw_text,
                now
            ))
            conn.commit()
            return cursor.lastrowid

    def update_channel_progress(self, channel_id: int, channel_title: str, channel_username: Optional[str], last_msg_id: int):
        """Updates channel scan tracking."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO channels (channel_id, channel_title, channel_username, last_msg_id, total_scraped, last_scraped_at)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    channel_title=excluded.channel_title,
                    channel_username=excluded.channel_username,
                    last_msg_id=MAX(channels.last_msg_id, excluded.last_msg_id),
                    total_scraped=(SELECT COUNT(*) FROM characters WHERE channel_id = excluded.channel_id),
                    last_scraped_at=excluded.last_scraped_at
            """, (channel_id, channel_title, channel_username, last_msg_id, now))
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        """Returns database statistics."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM characters")
            total_characters = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT anime) FROM characters WHERE anime != 'Unknown'")
            unique_animes = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM channels")
            total_channels = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM characters WHERE image_phash IS NOT NULL AND image_phash != ''")
            hashed_characters = cursor.fetchone()[0]

            return {
                "total_characters": total_characters,
                "hashed_characters": hashed_characters,
                "unique_animes": unique_animes,
                "total_channels": total_channels
            }

    def export_to_json(self, output_path: Path = EXPORT_JSON_PATH) -> int:
        """Exports pure lightweight JSON catalog with all visual hashes and cloud Telegram IDs."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM characters ORDER BY id ASC")
            rows = cursor.fetchall()

            data = []
            for row in rows:
                item = dict(row)
                if item.get("extra_info_json"):
                    try:
                        item["extra_info"] = json.loads(item["extra_info_json"])
                    except Exception:
                        item["extra_info"] = {}
                else:
                    item["extra_info"] = {}
                del item["extra_info_json"]
                data.append(item)

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            return len(data)

    def export_to_csv(self, output_path: Path = EXPORT_CSV_PATH) -> int:
        """Exports all characters to a CSV file."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, character_id, name, anime, rarity, event,
                       image_phash, channel_title, telegram_msg_id, scraped_at
                FROM characters ORDER BY id ASC
            """)
            rows = cursor.fetchall()
            if not rows:
                return 0

            with open(output_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "ID", "Character ID", "Name", "Anime / Source", "Rarity", "Event",
                    "Visual Hash (pHash)", "Channel Title", "Message ID", "Scraped At"
                ])
                for row in rows:
                    writer.writerow(list(row))

            return len(rows)
