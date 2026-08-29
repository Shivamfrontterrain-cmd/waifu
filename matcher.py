import io
import json
import logging
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from functools import lru_cache

from PIL import Image
import imagehash
from tqdm import tqdm

from config import DB_PATH, GITHUB_DATA_URL
from database import DatabaseManager

logger = logging.getLogger("WaifuMatcher")


def compute_image_hashes(img_input: Any) -> Optional[Tuple[str, str, int, int]]:
    """
    Computes pHash and dHash as both hex strings and fast 64-bit CPU integers.
    Accepts PIL Image, bytes, io.BytesIO, or file path.
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

        if img.mode != "RGB":
            img = img.convert("RGB")

        # Fast perceptual & difference hash computation
        p_obj = imagehash.phash(img)
        d_obj = imagehash.dhash(img)

        phash_str = str(p_obj)
        dhash_str = str(d_obj)
        phash_int = int(phash_str, 16)
        dhash_int = int(dhash_str, 16)

        return phash_str, dhash_str, phash_int, dhash_int
    except Exception as e:
        logger.debug(f"Error hashing image: {e}")
        return None


class WaifuMatcher:
    """Ultra-fast visual matcher using native CPU 64-bit POPCNT integer comparison."""

    def __init__(self, db_path: Path = DB_PATH, github_url: str = GITHUB_DATA_URL):
        self.github_url = github_url
        self.db_path = db_path
        self.db_manager = DatabaseManager(db_path)
        # Array of (phash_int, dhash_int, character_data_dict)
        self.index: List[Tuple[int, int, Dict[str, Any]]] = []
        self.load_index()

    def load_index_from_github(self) -> bool:
        """Loads all character data and hashes directly from GitHub raw JSON API."""
        try:
            logger.info(f"Connecting to GitHub Cloud Database: {self.github_url}...")
            req = urllib.request.Request(
                self.github_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WaifuBot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    raw_data = response.read().decode("utf-8")
                    characters = json.loads(raw_data)

                    loaded = []
                    for item in characters:
                        if not item.get("name") or item.get("name") == "Unknown":
                            continue
                        p_str = item.get("image_phash")
                        d_str = item.get("image_dhash")

                        if p_str:
                            try:
                                p_int = int(p_str, 16)
                                d_int = int(d_str, 16) if d_str else p_int
                                loaded.append((p_int, d_int, item))
                            except ValueError:
                                continue

                    if loaded:
                        self.index = loaded
                        logger.info(f"⚡ Ultra-Fast Index: {len(self.index):,} characters loaded in memory!")
                        return True
        except Exception as e:
            logger.warning(f"Could not load directly from GitHub ({e}). Checking local database...")
        return False

    def load_index(self):
        """Loads index: prioritizes GitHub Cloud Database, with local database fallback."""
        if self.load_index_from_github():
            return

        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, anime, rarity, character_id, event,
                           telegram_file_id, telegram_file_unique_id,
                           channel_title, image_phash, image_dhash
                    FROM characters
                    WHERE image_phash IS NOT NULL AND image_phash != '' AND name != 'Unknown'
                """)
                rows = cursor.fetchall()

                loaded = []
                for row in rows:
                    item = dict(row)
                    p_str = item.get("image_phash")
                    d_str = item.get("image_dhash")
                    if p_str:
                        try:
                            p_int = int(p_str, 16)
                            d_int = int(d_str, 16) if d_str else p_int
                            loaded.append((p_int, d_int, item))
                        except ValueError:
                            continue

                self.index = loaded
                logger.info(f"⚡ Loaded {len(self.index):,} characters into ultra-fast active CPU memory.")
        except Exception as e:
            logger.error(f"Error loading local index: {e}")

    def find_match(self, query_img: Any, max_distance: int = 14) -> Optional[Dict[str, Any]]:
        """
        Ultra-fast visual matching in < 1 millisecond using native CPU bit_count instructions.
        """
        if not self.index:
            self.load_index()
            if not self.index:
                return None

        hashes = compute_image_hashes(query_img)
        if not hashes:
            return None

        _, _, q_phash, q_dhash = hashes

        best_match = None
        min_distance = 999

        # Native CPU bitwise XOR + bit_count (POPCNT) loop
        for s_phash, s_dhash, char_data in self.index:
            dist_p = (q_phash ^ s_phash).bit_count()
            dist_d = (q_dhash ^ s_dhash).bit_count()
            combined_dist = (dist_p * 0.6) + (dist_d * 0.4)

            if combined_dist < min_distance:
                min_distance = combined_dist
                best_match = char_data
                # Instant exact match early break
                if min_distance == 0:
                    break

        if best_match and min_distance <= max_distance:
            confidence = max(0.0, min(100.0, (1.0 - (min_distance / 64.0)) * 100.0))
            result = dict(best_match)
            result["confidence"] = round(confidence, 1)
            result["hamming_distance"] = round(min_distance, 2)
            return result

        return None

    def search_by_name(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Searches characters by text query directly in memory."""
        if not query or not query.strip():
            return []
        q = query.strip().lower()

        results = []
        for _, _, char in self.index:
            name = char.get("name", "").lower()
            anime = char.get("anime", "").lower()
            if q in name or q in anime:
                results.append(char)
                if len(results) >= limit:
                    break
        return results
