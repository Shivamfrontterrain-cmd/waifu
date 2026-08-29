import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from game_config import (
    GAME_DB_PATH,
    DEFAULT_SPAWN_INTERVAL,
    INITIAL_BALANCE,
    DAILY_COIN_REWARD,
    SPAWN_TIMEOUT_SECONDS
)

logger = logging.getLogger("CatcherDB")


class GameDatabaseManager:
    """Manages player profiles, gacha inventories, chat spawn state, economy, and trades."""

    def __init__(self, db_path: Path = GAME_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Returns a WAL-enabled thread-safe SQLite connection."""
        conn = sqlite3.connect(self.db_path, timeout=60.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def init_db(self):
        """Initializes database schema and indexes."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Players Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    balance INTEGER DEFAULT 250,
                    total_rolls INTEGER DEFAULT 0,
                    total_claims INTEGER DEFAULT 0,
                    fav_character_id INTEGER,
                    last_daily TIMESTAMP,
                    joined_at TIMESTAMP
                )
            """)

            # Player Inventories Table (Harem Collection)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    character_id INTEGER NOT NULL,
                    count INTEGER DEFAULT 1,
                    obtained_at TIMESTAMP,
                    is_favorite INTEGER DEFAULT 0,
                    UNIQUE(user_id, character_id),
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)

            # Chat Groups Settings & Live Active Spawns
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY,
                    chat_title TEXT,
                    spawn_interval INTEGER DEFAULT 30,
                    message_counter INTEGER DEFAULT 0,
                    active_spawn_char_id INTEGER,
                    active_spawn_msg_id INTEGER,
                    active_spawn_time TIMESTAMP,
                    is_enabled INTEGER DEFAULT 1
                )
            """)

            # Trade Transactions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER NOT NULL,
                    receiver_id INTEGER NOT NULL,
                    sender_char_id INTEGER NOT NULL,
                    receiver_char_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP
                )
            """)

            # Indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_inv_user ON inventory(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_inv_char ON inventory(character_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_balance ON users(balance DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_claims ON users(total_claims DESC)")
            conn.commit()

    # ---------------- USER & PROFILE METHODS ---------------- #

    def get_or_create_user(self, user_id: int, username: Optional[str], first_name: str) -> Dict[str, Any]:
        """Gets existing player or creates a new profile with starting balance."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                # Update username / first_name if changed
                cursor.execute(
                    "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                    (username, first_name, user_id)
                )
                conn.commit()
                return dict(row)

            # Create new player profile
            cursor.execute("""
                INSERT INTO users (user_id, username, first_name, balance, joined_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, username, first_name, INITIAL_BALANCE, now))
            conn.commit()

            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            return dict(cursor.fetchone())

    def update_balance(self, user_id: int, delta: int) -> int:
        """Adds or deducts coins from a user's wallet. Returns updated balance."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, user_id))
            cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            conn.commit()
            return row["balance"] if row else 0

    def claim_daily(self, user_id: int) -> Tuple[bool, int, Optional[str]]:
        """
        Attempts to claim daily reward.
        Returns: (success: bool, coins_awarded: int, time_remaining_str: Optional[str])
        """
        now = datetime.now()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT last_daily FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if not row:
                return False, 0, None

            last_daily_str = row["last_daily"]
            if last_daily_str:
                last_daily = datetime.fromisoformat(last_daily_str)
                next_eligible = last_daily + timedelta(hours=20)
                if now < next_eligible:
                    diff = next_eligible - now
                    hours, remainder = divmod(int(diff.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    return False, 0, f"{hours}h {minutes}m"

            # Award daily reward
            cursor.execute("""
                UPDATE users 
                SET balance = balance + ?, last_daily = ?
                WHERE user_id = ?
            """, (DAILY_COIN_REWARD, now.isoformat(), user_id))
            conn.commit()
            return True, DAILY_COIN_REWARD, None

    def set_favorite(self, user_id: int, character_id: int) -> bool:
        """Sets a player's featured favorite character."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Verify user owns the character
            cursor.execute("SELECT 1 FROM inventory WHERE user_id = ? AND character_id = ?", (user_id, character_id))
            if not cursor.fetchone():
                return False

            cursor.execute("UPDATE users SET fav_character_id = ? WHERE user_id = ?", (character_id, user_id))
            cursor.execute("UPDATE inventory SET is_favorite = (character_id = ?) WHERE user_id = ?", (character_id, user_id))
            conn.commit()
            return True

    # ---------------- INVENTORY & HAREM METHODS ---------------- #

    def add_to_inventory(self, user_id: int, character_id: int, source: str = "claim") -> int:
        """
        Adds a character to the user's harem collection or increments count.
        Returns the new total count of this character owned by the user.
        """
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO inventory (user_id, character_id, count, obtained_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(user_id, character_id) DO UPDATE SET count = count + 1
            """, (user_id, character_id, now))

            if source == "claim":
                cursor.execute("UPDATE users SET total_claims = total_claims + 1 WHERE user_id = ?", (user_id,))
            elif source == "roll":
                cursor.execute("UPDATE users SET total_rolls = total_rolls + 1 WHERE user_id = ?", (user_id,))

            cursor.execute("SELECT count FROM inventory WHERE user_id = ? AND character_id = ?", (user_id, character_id))
            row = cursor.fetchone()
            conn.commit()
            return row["count"] if row else 1

    def remove_from_inventory(self, user_id: int, character_id: int, count: int = 1) -> bool:
        """Removes character(s) from a user's inventory (e.g. for trades or sales)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count FROM inventory WHERE user_id = ? AND character_id = ?", (user_id, character_id))
            row = cursor.fetchone()
            if not row or row["count"] < count:
                return False

            if row["count"] == count:
                cursor.execute("DELETE FROM inventory WHERE user_id = ? AND character_id = ?", (user_id, character_id))
            else:
                cursor.execute("UPDATE inventory SET count = count - ? WHERE user_id = ? AND character_id = ?", (count, user_id, character_id))

            conn.commit()
            return True

    def get_user_inventory(self, user_id: int) -> List[Dict[str, Any]]:
        """Returns all character IDs and counts owned by a user."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT character_id, count, obtained_at, is_favorite
                FROM inventory
                WHERE user_id = ?
                ORDER BY is_favorite DESC, obtained_at DESC
            """, (user_id,))
            return [dict(r) for r in cursor.fetchall()]

    def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """Returns player summary stats."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            u = cursor.fetchone()
            if not u:
                return {}

            cursor.execute("SELECT COUNT(*), SUM(count) FROM inventory WHERE user_id = ?", (user_id,))
            inv_stats = cursor.fetchone()
            unique_chars = inv_stats[0] or 0
            total_cards = inv_stats[1] or 0

            # Get player global rank
            cursor.execute("SELECT COUNT(*) + 1 FROM users WHERE total_claims > ?", (u["total_claims"],))
            rank = cursor.fetchone()[0]

            res = dict(u)
            res["unique_characters"] = unique_chars
            res["total_cards"] = total_cards
            res["rank"] = rank
            return res

    # ---------------- CHAT SPAWN & COUNTERS ---------------- #

    def register_chat_message(self, chat_id: int, chat_title: str) -> bool:
        """
        Increments the message counter for a group chat.
        Returns True if the counter hit the spawn threshold and a new waifu should spawn!
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,))
            chat = cursor.fetchone()

            if not chat:
                cursor.execute("""
                    INSERT INTO chats (chat_id, chat_title, spawn_interval, message_counter, is_enabled)
                    VALUES (?, ?, ?, 1, 1)
                """, (chat_id, chat_title, DEFAULT_SPAWN_INTERVAL))
                conn.commit()
                return False

            if not chat["is_enabled"]:
                return False

            # If there is already an active unclaimed spawn that hasn't timed out, don't spawn a new one yet
            if chat["active_spawn_char_id"] and chat["active_spawn_time"]:
                spawn_time = datetime.fromisoformat(chat["active_spawn_time"])
                if (datetime.now() - spawn_time).total_seconds() < SPAWN_TIMEOUT_SECONDS:
                    return False

            new_count = chat["message_counter"] + 1
            if new_count >= chat["spawn_interval"]:
                # Reset counter and trigger spawn
                cursor.execute("""
                    UPDATE chats 
                    SET message_counter = 0, chat_title = ?
                    WHERE chat_id = ?
                """, (chat_title, chat_id))
                conn.commit()
                return True
            else:
                cursor.execute("""
                    UPDATE chats 
                    SET message_counter = ?, chat_title = ?
                    WHERE chat_id = ?
                """, (new_count, chat_title, chat_id))
                conn.commit()
                return False

    def set_active_spawn(self, chat_id: int, character_id: int, message_id: int):
        """Sets the active wild waifu spawn in a group."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE chats 
                SET active_spawn_char_id = ?, active_spawn_msg_id = ?, active_spawn_time = ?
                WHERE chat_id = ?
            """, (character_id, message_id, now, chat_id))
            conn.commit()

    def get_active_spawn(self, chat_id: int) -> Optional[Tuple[int, int]]:
        """
        Gets the active spawn for a chat.
        Returns (character_id, message_id) or None if no active spawn / expired.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT active_spawn_char_id, active_spawn_msg_id, active_spawn_time FROM chats WHERE chat_id = ?", (chat_id,))
            row = cursor.fetchone()
            if not row or not row["active_spawn_char_id"]:
                return None

            if row["active_spawn_time"]:
                spawn_time = datetime.fromisoformat(row["active_spawn_time"])
                if (datetime.now() - spawn_time).total_seconds() >= SPAWN_TIMEOUT_SECONDS:
                    self.clear_active_spawn(chat_id)
                    return None

            return row["active_spawn_char_id"], row["active_spawn_msg_id"]

    def clear_active_spawn(self, chat_id: int):
        """Clears active spawn after being claimed or expired."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE chats 
                SET active_spawn_char_id = NULL, active_spawn_msg_id = NULL, active_spawn_time = NULL
                WHERE chat_id = ?
            """, (chat_id,))
            conn.commit()

    def set_chat_interval(self, chat_id: int, interval: int) -> bool:
        """Sets custom message interval for a chat."""
        interval = max(5, min(interval, 500))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE chats SET spawn_interval = ? WHERE chat_id = ?", (interval, chat_id))
            conn.commit()
            return True

    def toggle_chat_spawns(self, chat_id: int) -> bool:
        """Toggles wild spawns on or off for a chat."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_enabled FROM chats WHERE chat_id = ?", (chat_id,))
            row = cursor.fetchone()
            new_state = 0 if (row and row["is_enabled"]) else 1
            cursor.execute("UPDATE chats SET is_enabled = ? WHERE chat_id = ?", (new_state, chat_id))
            conn.commit()
            return bool(new_state)

    # ---------------- LEADERBOARDS ---------------- #

    def get_top_collectors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns leaderboard of top waifu catchers."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.user_id, u.username, u.first_name, u.total_claims,
                       COUNT(i.id) as unique_chars, SUM(i.count) as total_cards
                FROM users u
                LEFT JOIN inventory i ON u.user_id = i.user_id
                GROUP BY u.user_id
                ORDER BY u.total_claims DESC, total_cards DESC
                LIMIT ?
            """, (limit,))
            return [dict(r) for r in cursor.fetchall()]

    def get_top_rich(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns leaderboard of wealthiest players."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT user_id, username, first_name, balance, total_claims
                FROM users
                ORDER BY balance DESC
                LIMIT ?
            """, (limit,))
            return [dict(r) for r in cursor.fetchall()]
