import sqlite3
import io
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from PIL import Image
import imagehash
from tqdm import tqdm

from config import DB_PATH, IMAGE_DIR
from database import DatabaseManager

logger = logging.getLogger("WaifuMatcher")


def compute_image_hashes(img_input: Any) -> Optional[Tuple[str, str]]:
    """
    Computes both pHash (Perceptual Hash) and dHash (Difference Hash) for an image.
    Accepts a PIL Image, bytes, io.BytesIO, or file path.
    """
    try:
        if isinstance(img_input, (str, Path)):
            img = Image.open(img_input)
        elif isinstance(img_input, (bytes, bytearray)):
            img = Image.open(io.BytesIO(img_input))
        elif isinstance(img_input, io.BytesIO):
            img = Image.open(img_input)
        elif isinstance(img_input, Image.Image):
            img = img_input
        else:
            return None

        # Convert to RGB to ensure consistent hashing
        if img.mode != "RGB":
            img = img.convert("RGB")

        phash_str = str(imagehash.phash(img))
        dhash_str = str(imagehash.dhash(img))
        return phash_str, dhash_str
    except Exception as e:
        logger.debug(f"Error hashing image: {e}")
        return None


class WaifuMatcher:
    """High-speed visual matcher that identifies anime characters by image."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_manager = DatabaseManager(db_path)
        self.db_path = db_path
        self._ensure_hash_columns()
        # In-memory index: list of (phash_obj, dhash_obj, character_data_dict)
        self.index: List[Tuple[Any, Any, Dict[str, Any]]] = []
        self.load_index()

    def _ensure_hash_columns(self):
        """Adds image_phash and image_dhash columns to characters table if missing."""
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(characters)")
            columns = [row["name"] for row in cursor.fetchall()]

            if "image_phash" not in columns:
                cursor.execute("ALTER TABLE characters ADD COLUMN image_phash TEXT")
            if "image_dhash" not in columns:
                cursor.execute("ALTER TABLE characters ADD COLUMN image_dhash TEXT")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_char_phash ON characters(image_phash)")
            conn.commit()

    def build_hash_index(self, force_recompute: bool = False) -> int:
        """
        Scans all downloaded images in the database, computes hashes,
        and saves them into SQLite for instant future lookups.
        """
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor()
            if force_recompute:
                cursor.execute("SELECT id, image_path, name FROM characters WHERE image_path IS NOT NULL AND name != 'Unknown'")
            else:
                cursor.execute(
                    "SELECT id, image_path, name FROM characters "
                    "WHERE image_path IS NOT NULL AND (image_phash IS NULL OR image_phash = '') AND name != 'Unknown'"
                )
            rows = cursor.fetchall()

            if not rows:
                logger.info("All character images are already hashed and indexed.")
                self.load_index()
                return 0

            logger.info(f"Computing perceptual hashes for {len(rows)} character images...")
            updates = []
            for row in tqdm(rows, desc="Indexing Images"):
                char_id = row["id"]
                img_path = Path(row["image_path"])

                if img_path.exists():
                    hashes = compute_image_hashes(img_path)
                    if hashes:
                        phash_val, dhash_val = hashes
                        updates.append((phash_val, dhash_val, char_id))

            if updates:
                cursor.executemany(
                    "UPDATE characters SET image_phash = ?, image_dhash = ? WHERE id = ?",
                    updates
                )
                conn.commit()
                logger.info(f"Successfully indexed {len(updates)} character images!")

        self.load_index()
        return len(updates)

    def load_index(self):
        """Loads all hashed characters into an ultra-fast in-memory index."""
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, anime, rarity, character_id, event,
                       image_filename, image_path, channel_title,
                       image_phash, image_dhash
                FROM characters
                WHERE image_phash IS NOT NULL AND image_phash != '' AND name != 'Unknown'
            """)
            rows = cursor.fetchall()

            loaded = []
            for row in rows:
                item = dict(row)
                try:
                    phash_obj = imagehash.hex_to_hash(item["image_phash"])
                    dhash_obj = imagehash.hex_to_hash(item["image_dhash"]) if item.get("image_dhash") else None
                    loaded.append((phash_obj, dhash_obj, item))
                except Exception:
                    continue

            self.index = loaded
            logger.info(f"Loaded {len(self.index)} hashed characters into active memory index.")

    def find_match(self, query_img: Any, max_distance: int = 14) -> Optional[Dict[str, Any]]:
        """
        Compares an incoming image against the database in milliseconds.
        Returns the closest matching character metadata + confidence score.
        """
        if not self.index:
            self.load_index()
            if not self.index:
                return None

        hashes = compute_image_hashes(query_img)
        if not hashes:
            return None

        query_phash_str, query_dhash_str = hashes
        query_phash = imagehash.hex_to_hash(query_phash_str)
        query_dhash = imagehash.hex_to_hash(query_dhash_str)

        best_match = None
        min_distance = 999

        for phash_obj, dhash_obj, char_data in self.index:
            # Hamming distance calculation (0 = 100% identical, <= 10 = extremely high match)
            dist_p = query_phash - phash_obj
            dist_d = (query_dhash - dhash_obj) if (dhash_obj and query_dhash) else dist_p
            combined_dist = (dist_p * 0.6) + (dist_d * 0.4)

            if combined_dist < min_distance:
                min_distance = combined_dist
                best_match = char_data
                # Early return on exact match
                if min_distance == 0:
                    break

        if best_match and min_distance <= max_distance:
            # Calculate match percentage (64-bit hash)
            confidence = max(0.0, min(100.0, (1.0 - (min_distance / 64.0)) * 100.0))
            result = dict(best_match)
            result["confidence"] = round(confidence, 1)
            result["hamming_distance"] = round(min_distance, 2)
            return result

        return None

    def search_by_name(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Searches characters by text name or anime title."""
        if not query or not query.strip():
            return []
        term = f"%{query.strip()}%"
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, anime, rarity, character_id, event,
                       image_filename, image_path, channel_title
                FROM characters
                WHERE (name LIKE ? OR anime LIKE ?) AND name != 'Unknown'
                ORDER BY CASE WHEN name LIKE ? THEN 0 ELSE 1 END, id ASC
                LIMIT ?
            """, (term, term, term, limit))
            return [dict(r) for r in cursor.fetchall()]
