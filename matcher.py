import io
import json
import logging
import random
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

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
    """Multi-layer recognition engine: O(1) Unique ID mapping, CPU POPCNT Visual Search & Fuzzy Text Index."""

    def __init__(self, db_path: Path = DB_PATH, github_url: str = GITHUB_DATA_URL):
        self.github_url = github_url
        self.db_path = db_path
        self.db_manager = DatabaseManager(db_path)
        # Unique ID hashmap for instant O(1) Telegram forward matching
        self.unique_id_map: Dict[str, Dict[str, Any]] = {}
        # Array of (phash_int, dhash_int, character_data_dict) for visual recognition
        self.visual_index: List[Tuple[int, int, Dict[str, Any]]] = []
        # All loaded character records
        self.all_characters: List[Dict[str, Any]] = []
        self.load_index()

    def load_index_from_github(self) -> bool:
        """Loads all character data and hashes directly from GitHub raw JSON API."""
        try:
            logger.info(f"Connecting to GitHub Cloud Database: {self.github_url}...")
            req = urllib.request.Request(
                self.github_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WaifuBot/2.0"}
            )
            with urllib.request.urlopen(req, timeout=12) as response:
                if response.status == 200:
                    raw_data = response.read().decode("utf-8")
                    characters = json.loads(raw_data)

                    v_loaded = []
                    u_map = {}
                    all_chars = []

                    for item in characters:
                        name = item.get("name")
                        if not name or name == "Unknown":
                            continue

                        all_chars.append(item)

                        # Unique ID Map
                        uid = item.get("telegram_file_unique_id")
                        if uid:
                            u_map[str(uid)] = item

                        # Visual Hashes
                        p_str = item.get("image_phash")
                        d_str = item.get("image_dhash")
                        if p_str:
                            try:
                                p_int = int(p_str, 16)
                                d_int = int(d_str, 16) if d_str else p_int
                                v_loaded.append((p_int, d_int, item))
                            except ValueError:
                                continue

                    if all_chars:
                        self.visual_index = v_loaded
                        self.unique_id_map = u_map
                        self.all_characters = all_chars
                        logger.info(
                            f"✅ Loaded {len(self.all_characters):,} characters from GitHub Cloud Database! "
                            f"(Visual: {len(self.visual_index):,}, Unique IDs: {len(self.unique_id_map):,})"
                        )
                        return True
        except Exception as e:
            logger.warning(f"Could not load directly from GitHub ({e}). Checking local database...")
        return False

    def load_index_from_local(self) -> bool:
        """Loads all character data and hashes directly from local SQLite database."""
        try:
            if not self.db_path.exists():
                return False

            with self.db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, anime, rarity, character_id, event,
                           telegram_file_id, telegram_file_unique_id,
                           channel_title, image_phash, image_dhash
                    FROM characters
                    WHERE name IS NOT NULL AND name != 'Unknown'
                """)
                rows = cursor.fetchall()

                if not rows:
                    return False

                v_loaded = []
                u_map = {}
                all_chars = []

                for row in rows:
                    item = dict(row)
                    all_chars.append(item)

                    uid = item.get("telegram_file_unique_id")
                    if uid:
                        u_map[str(uid)] = item

                    p_str = item.get("image_phash")
                    d_str = item.get("image_dhash")
                    if p_str:
                        try:
                            p_int = int(p_str, 16)
                            d_int = int(d_str, 16) if d_str else p_int
                            v_loaded.append((p_int, d_int, item))
                        except ValueError:
                            continue

                self.visual_index = v_loaded
                self.unique_id_map = u_map
                self.all_characters = all_chars
                logger.info(
                    f"⚡ Loaded {len(self.all_characters):,} characters directly from local SQLite database! "
                    f"(Visual: {len(self.visual_index):,}, Unique IDs: {len(self.unique_id_map):,})"
                )
                return True
        except Exception as e:
            logger.debug(f"Could not load local database ({e}). Trying GitHub fallback...")
            return False

    def load_index(self):
        """Loads index: prioritizes local SQLite database, falls back to GitHub Cloud if local is absent."""
        # 1. Prioritize live local database
        if self.load_index_from_local():
            return

        # 2. Fallback to GitHub Cloud Database
        if self.load_index_from_github():
            return

        logger.warning("No character records found in local database or GitHub cloud.")

    def find_match_by_unique_id(self, unique_id: str) -> Optional[Dict[str, Any]]:
        """Instant O(1) matching for forwarded images using Telegram file_unique_id."""
        if not unique_id:
            return None
        match = self.unique_id_map.get(str(unique_id))
        if match:
            res = dict(match)
            res["confidence"] = 100.0
            res["match_type"] = "Cloud Telegram Unique ID Match"
            return res
        return None

    def find_match_by_image(self, query_img: Any, max_distance: int = 14) -> Optional[Dict[str, Any]]:
        """Hardware-accelerated CPU POPCNT visual matching in < 1 millisecond."""
        if not self.visual_index:
            return None

        hashes = compute_image_hashes(query_img)
        if not hashes:
            return None

        _, _, q_phash, q_dhash = hashes

        best_match = None
        min_distance = 999

        for s_phash, s_dhash, char_data in self.visual_index:
            dist_p = (q_phash ^ s_phash).bit_count()
            dist_d = (q_dhash ^ s_dhash).bit_count()
            combined_dist = (dist_p * 0.6) + (dist_d * 0.4)

            if combined_dist < min_distance:
                min_distance = combined_dist
                best_match = char_data
                if min_distance == 0:
                    break

        if best_match and min_distance <= max_distance:
            confidence = max(0.0, min(100.0, (1.0 - (min_distance / 64.0)) * 100.0))
            result = dict(best_match)
            result["confidence"] = round(confidence, 1)
            result["hamming_distance"] = round(min_distance, 2)
            result["match_type"] = "Perceptual Visual Recognition"
            return result

        return None

    def search_by_name(self, query: str, limit: int = 6) -> List[Dict[str, Any]]:
        """Searches characters by name, anime, or ID directly in memory."""
        if not query or not query.strip():
            return []
        q = query.strip().lower()

        exact_matches = []
        partial_matches = []

        for char in self.all_characters:
            name = (char.get("name") or "").lower()
            anime = (char.get("anime") or "").lower()
            cid = str(char.get("character_id") or "").lower()

            if q == name or q == cid:
                exact_matches.append(char)
            elif q in name or q in anime or q in cid:
                partial_matches.append(char)

            if len(exact_matches) + len(partial_matches) >= limit * 2:
                break

        combined = exact_matches + partial_matches
        return combined[:limit]

    def search_by_anime(self, anime_query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns all characters belonging to a specific anime series."""
        if not anime_query or not anime_query.strip():
            return []
        q = anime_query.strip().lower()
        results = []
        for char in self.all_characters:
            anime = (char.get("anime") or "").lower()
            if q in anime:
                results.append(char)
                if len(results) >= limit:
                    break
        return results

    def get_character_by_id(self, char_id: str) -> Optional[Dict[str, Any]]:
        """Finds a character by their exact character ID."""
        if not char_id:
            return None
        target = str(char_id).strip().lower().lstrip("#")
        for char in self.all_characters:
            cid = str(char.get("character_id") or "").strip().lower().lstrip("#")
            if cid == target:
                return char
        return None

    def get_random_character(self) -> Optional[Dict[str, Any]]:
        """Returns a random character from the loaded catalog."""
        if not self.all_characters:
            return None
        return random.choice(self.all_characters)

    def reload(self):
        """Refreshes the database catalog into memory."""
        self.load_index()
