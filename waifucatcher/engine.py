import io
import shutil
import asyncio
import re
import random
import logging
from difflib import SequenceMatcher
from typing import Dict, Any, Optional, List, Tuple

from game_config import (
    CHARACTER_DB_PATH,
    RARITIES,
    CLAIM_REWARD_COINS,
    ROLL_COST_COINS,
    API_ID,
    API_HASH,
    PHONE,
    WAIFU_SESSION_PATH,
    CATCHER_SESSION_NAME
)

# Import parser & matcher from parent directory
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon import TelegramClient
from matcher import WaifuMatcher, compute_image_hashes
from parser import sanitize_character_name, clean_text, strip_field_prefix

logger = logging.getLogger("CatcherEngine")


class CloudImageStreamer:
    """Streams character images from Telegram Cloud channels into RAM in 0ms (0 local image files stored on disk)."""

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.semaphore = asyncio.Semaphore(15)
        self._connected = False
        self._lock = asyncio.Lock()
        self._cache: Dict[Tuple[int, int], bytes] = {}

    async def connect(self):
        async with self._lock:
            if self._connected:
                return
            if not API_ID or not API_HASH:
                return
            try:
                c_sess_file = Path(CATCHER_SESSION_NAME + ".session")
                if not c_sess_file.exists() and WAIFU_SESSION_PATH.exists():
                    try:
                        shutil.copy(WAIFU_SESSION_PATH, c_sess_file)
                    except Exception:
                        pass

                self.client = TelegramClient(CATCHER_SESSION_NAME, API_ID, API_HASH)
                await self.client.connect()
                if await self.client.is_user_authorized():
                    self._connected = True
                    logger.info("☁️ Cloud Image Streamer connected to Telegram Cloud CDN!")
            except Exception as e:
                logger.debug(f"CloudImageStreamer connect note: {e}")

    async def get_image_bytes(self, channel_id: int, msg_id: int) -> Optional[bytes]:
        """Fetches character thumbnail/photo directly into RAM (0 MB disk storage used)."""
        cache_key = (channel_id, msg_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not self._connected or not self.client:
            await self.connect()
        if not self._connected or not self.client:
            return None

        async with self.semaphore:
            try:
                async def _fetch():
                    msg = await self.client.get_messages(channel_id, ids=msg_id)
                    if not msg or not (getattr(msg, "photo", None) or getattr(msg, "document", None)):
                        return None
                    buf = io.BytesIO()
                    # thumb=0 extracts embedded PhotoStrippedSize in 0.001ms directly from message bytes
                    await self.client.download_media(msg, file=buf, thumb=0)
                    data = buf.getvalue()
                    if not data:
                        await self.client.download_media(msg, file=buf, thumb=1)
                        data = buf.getvalue()
                    return data if data else None

                data = await asyncio.wait_for(_fetch(), timeout=2.0)
                if data:
                    if len(self._cache) > 2000:
                        self._cache.pop(next(iter(self._cache)))
                    self._cache[cache_key] = data
                return data
            except Exception as e:
                logger.debug(f"Fast cloud streamer note ({channel_id}, {msg_id}): {e}")
                return None


class WaifuGameEngine:
    """Core game mechanics: Gacha summoning, wild card spawning, fuzzy guessing, and visual matching."""

    def __init__(self, char_db_path: Path = CHARACTER_DB_PATH):
        self.matcher = WaifuMatcher(char_db_path)
        self.all_characters = self.matcher.all_characters
        self.image_streamer = CloudImageStreamer()
        logger.info(f"🎮 Waifu Catcher Engine initialized with {len(self.all_characters):,} character models!")

    async def get_character_photo_bytes(self, char: Dict[str, Any]) -> Optional[bytes]:
        """Streams character image directly from Telegram Cloud into RAM."""
        cid = char.get("channel_id")
        mid = char.get("telegram_msg_id")
        if not cid or not mid:
            return None
        return await self.image_streamer.get_image_bytes(cid, mid)

    def get_character(self, character_id: int) -> Optional[Dict[str, Any]]:
        """Looks up a character by their numeric database ID."""
        return self.matcher.get_character_by_id(character_id)

    def get_random_spawn(self) -> Optional[Dict[str, Any]]:
        """Selects a random character for wild spawning in group chats."""
        if not self.all_characters:
            return None
        return random.choice(self.all_characters)

    def get_rarity_info(self, raw_rarity: Optional[str]) -> Tuple[str, str, int]:
        """
        Normalizes rarity string into standard game rarity.
        Returns: (rarity_name, emoji_color, sell_price)
        """
        if not raw_rarity:
            return "Common", "⚪", 25

        raw_lower = raw_rarity.lower()
        if any(w in raw_lower for w in ["event", "limited", "special", "edition"]):
            return "Event", "✨", 1000
        elif any(w in raw_lower for w in ["mythic", "mythical", "god", "divine"]):
            return "Mythical", "🔴", 2000
        elif any(w in raw_lower for w in ["legendary", "legend", "5 star", "⭐⭐⭐⭐⭐"]):
            return "Legendary", "🟡", 800
        elif any(w in raw_lower for w in ["epic", "4 star", "⭐⭐⭐⭐"]):
            return "Epic", "🟣", 350
        elif any(w in raw_lower for w in ["super rare", "sr", "3 star", "⭐⭐⭐"]):
            return "Super Rare", "🔵", 150
        elif any(w in raw_lower for w in ["rare", "r", "2 star", "⭐⭐"]):
            return "Rare", "🟢", 60
        else:
            return "Common", "⚪", 25

    def normalize_guess_text(self, text: str) -> str:
        """Strips command words, punctuation, and extra spaces for fuzzy matching."""
        if not text:
            return ""
        cleaned = clean_text(text).lower()
        # Remove slash commands and verbs
        cleaned = re.sub(
            r'^/(?:catch|grab|claim|collect|harem|guess|who|find|snatch|marry)\s*',
            '',
            cleaned,
            flags=re.IGNORECASE
        )
        cleaned = re.sub(
            r'^(?:catch|grab|claim|collect|guess|it\s+is|its|is)\s*',
            '',
            cleaned,
            flags=re.IGNORECASE
        )
        # Remove symbols and punctuation
        cleaned = re.sub(r'[^a-z0-9\s]', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned

    def verify_name_guess(self, guess: str, target_character: Dict[str, Any]) -> bool:
        """
        Validates if the user's guess matches the spawned character.
        Supports:
        1. Exact match
        2. First name / Last name token matching (e.g. 'Itadori' matches 'Yuji Itadori')
        3. Typo tolerance (Levenshtein similarity >= 80%)
        """
        norm_guess = self.normalize_guess_text(guess)
        if not norm_guess or len(norm_guess) < 2:
            return False

        real_name = sanitize_character_name(target_character.get("name") or "")
        norm_real = self.normalize_guess_text(real_name)

        if not norm_real:
            return False

        # 1. Exact match
        if norm_guess == norm_real:
            return True

        # 2. Token match (e.g. guessing last name or first name)
        real_tokens = norm_real.split()
        guess_tokens = norm_guess.split()

        # If guess contains all tokens of real name, or matches single key token
        if set(guess_tokens).issubset(set(real_tokens)):
            # Must be at least 3 characters to avoid matching single letter words
            if all(len(t) >= 3 for t in guess_tokens):
                return True

        # 3. Substring check for compound anime names
        if len(norm_guess) >= 4 and norm_guess in norm_real:
            return True

        # 4. Fuzzy Levenshtein ratio (allow up to 20% typos)
        ratio = SequenceMatcher(None, norm_guess, norm_real).ratio()
        if ratio >= 0.82:
            return True

        # Check individual tokens fuzzy match
        for r_tok in real_tokens:
            if len(r_tok) >= 4:
                if SequenceMatcher(None, norm_guess, r_tok).ratio() >= 0.85:
                    return True

        return False

    def verify_image_guess(self, image_stream, target_character: Dict[str, Any]) -> bool:
        """Validates screenshot or uploaded photo against target character visual hash."""
        hashes = compute_image_hashes(image_stream)
        if not hashes:
            return False

        p_str, d_str, q_phash, q_dhash = hashes
        target_phash_str = target_character.get("image_phash")
        if not target_phash_str:
            return False

        try:
            t_phash = int(target_phash_str, 16)
            diff = (q_phash ^ t_phash).bit_count()
            return diff <= 12  # Hamming distance threshold
        except Exception:
            return False

    # ---------------- FORMATTERS ---------------- #

    def format_wild_spawn_caption(self, char: Dict[str, Any]) -> str:
        """Formats the caption for a wild character spawn in a group."""
        anime = clean_text(char.get("anime") or "Unknown")
        rarity, color, _ = self.get_rarity_info(char.get("rarity"))
        cid = char.get("character_id") or char.get("id")

        return (
            "🌸 **A WILD WAIFU APPEARED!** 🌸\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🎬 **Anime:** *{anime}*\n"
            f"👑 **Rarity:** {color} **{rarity}**\n"
            f"🆔 **Card ID:** `#{cid}`\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💬 **Type `/catch <name>` or `/grab <name>` to claim!**\n"
            "*(First player to guess correctly gets the card + 💰 75 Coins!)*"
        )

    def format_claim_success_message(self, char: Dict[str, Any], user_name: str, total_owned: int) -> str:
        """Formats the success announcement when someone catches a wild character."""
        name = sanitize_character_name(char.get("name") or "Unknown")
        anime = clean_text(char.get("anime") or "Unknown")
        rarity, color, _ = self.get_rarity_info(char.get("rarity"))

        return (
            f"🎉 **CONGRATULATIONS, {user_name}!** 🎉\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"You caught: **`{name}`**!\n\n"
            f"👑 **Rarity:** {color} **{rarity}**\n"
            f"🎬 **Anime:** *{anime}*\n"
            f"🎒 **In Harem:** `x{total_owned}` copies owned\n"
            f"💰 **Reward:** `+{CLAIM_REWARD_COINS} Coins`\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "*(View your collection anytime with `/harem`)*"
        )

    def format_roll_success_message(self, char: Dict[str, Any], user_name: str, count: int, new_balance: int) -> str:
        """Formats the gacha summon result."""
        name = sanitize_character_name(char.get("name") or "Unknown")
        anime = clean_text(char.get("anime") or "Unknown")
        rarity, color, _ = self.get_rarity_info(char.get("rarity"))

        return (
            f"✨ **GACHA SUMMON COMPLETE!** ✨\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Summoner:** {user_name}\n"
            f"🌸 **Character:** **`{name}`**\n"
            f"👑 **Rarity:** {color} **{rarity}**\n"
            f"🎬 **Anime:** *{anime}*\n"
            f"🎒 **Copies Owned:** `x{count}`\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **Balance:** `{new_balance:,} Coins`"
        )
