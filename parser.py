import re
import html
import unicodedata
from typing import Dict, Any, Optional


def normalize_unicode_fonts(text: str) -> str:
    """Normalizes stylized Unicode mathematical/script/fraktur/bold/italic text into plain ASCII/Unicode."""
    if not text:
        return ""
    # NFKD decomposes stylized characters (e.g. 𝐑𝘆𝗼𝗺𝗲𝗻 -> Ryomen)
    return unicodedata.normalize('NFKD', text)


def clean_text(text: str) -> str:
    """Removes telegram markdown artifacts, extra spaces, and unescapes HTML."""
    if not text:
        return ""
    text = normalize_unicode_fonts(text)
    # Strip markdown symbols like **, __, ``, ~~
    text = re.sub(r'[*_`~]+', '', text)
    text = html.unescape(text)
    # Remove zero-width spaces & non-printing characters
    text = text.replace('\u200b', '').replace('\u200e', '').replace('\u200f', '').replace('\ufeff', '')
    return text.strip()


def strip_field_prefix(val: str, field_names: list) -> str:
    """Strips accidental lingering prefixes like 'Name:', 'Anime:', '👤', etc. from extracted values."""
    if not val:
        return val
    cleaned = clean_text(val)
    # Remove emojis at start
    cleaned = re.sub(r'^[^\w\s\(\)\[\]]+', '', cleaned).strip()
    # Remove field keywords
    for fn in field_names:
        pat = rf'^(?:{fn})\s*[:：\-—=]\s*'
        cleaned = re.sub(pat, '', cleaned, flags=re.IGNORECASE).strip()
    return cleaned.strip(' -:—=')


def sanitize_filename(name: str, max_length: int = 60) -> str:
    """Sanitizes strings to be valid file names across Windows and POSIX."""
    if not name:
        return "unnamed"
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    sanitized = re.sub(r'\s+', '_', sanitized).strip('._- ')
    if not sanitized:
        return "unnamed"
    return sanitized[:max_length]


class WaifuParser:
    """Smart parser for extracting waifu/character metadata from message captions/text."""

    # Common Key Regex Patterns (Applied after NFKD normalization)
    NAME_PATTERNS = [
        r'(?:name|character|waifu|husbando|char|hero|heroine)\s*[:：\-—=]\s*(.+)',
        r'(?:🌸|👤|💮|🎀|💖|✨|🪄|👧|👩)\s*(?:name|character)?\s*[:：\-—]?\s*(.+)',
    ]

    ANIME_PATTERNS = [
        r'(?:anime|source|from|series|origin|franchise|manga|game|show)\s*[:：\-—=]\s*(.+)',
        r'(?:🎬|📺|🎥|📖|🎮|🌐|⛩️)\s*(?:anime|source|from)?\s*[:：\-—]?\s*(.+)',
    ]

    RARITY_PATTERNS = [
        r'(?:rarity|tier|rank|stars|rating|grade)\s*[:：\-—=]\s*(.+)',
        r'(?:👑|⭐|🌟|💎|🏆|🔮|🎖️|🎗️)\s*(?:rarity|tier)?\s*[:：\-—]?\s*(.+)',
    ]

    ID_PATTERNS = [
        r'(?:id|cid|code|char_id|number|no\.?)\s*[:：\-—=]\s*#?([A-Za-z0-9_-]+)',
        r'(?:🆔|🔢|🏷️|🔖)\s*(?:id|code)?\s*[:：\-—]?\s*#?([A-Za-z0-9_-]+)',
    ]

    EVENT_PATTERNS = [
        r'(?:event|edition|version|type|theme)\s*[:：\-—=]\s*(.+)',
        r'(?:🎪|🎉|🎃|🎄|🏖️|👘)\s*(?:event|edition)?\s*[:：\-—]?\s*(.+)',
    ]

    @classmethod
    def parse(cls, raw_caption: Optional[str]) -> Dict[str, Any]:
        """
        Parses raw message caption/text into structured metadata.
        Returns a dictionary with extracted fields.
        """
        if not raw_caption or not raw_caption.strip():
            return {
                "name": "Unknown",
                "anime": "Unknown",
                "rarity": None,
                "character_id": None,
                "event": None,
                "extra_info": {},
                "raw_text": ""
            }

        # Normalize unicode fonts across the whole text
        normalized_raw = normalize_unicode_fonts(raw_caption.strip())
        lines = [line.strip() for line in normalized_raw.splitlines() if line.strip()]

        name = None
        anime = None
        rarity = None
        character_id = None
        event = None
        extra_info: Dict[str, str] = {}

        # 1. Line-by-line regex key matching
        for line in lines:
            cleaned_line = clean_text(line)

            # Check ID
            if not character_id:
                for pat in cls.ID_PATTERNS:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        character_id = clean_text(m.group(1))
                        break

            # Check Name
            if not name:
                for pat in cls.NAME_PATTERNS:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        name = strip_field_prefix(m.group(1), ["name", "character", "waifu", "char"])
                        break

            # Check Anime
            if not anime:
                for pat in cls.ANIME_PATTERNS:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        anime = strip_field_prefix(m.group(1), ["anime", "source", "from", "series", "origin"])
                        break

            # Check Rarity
            if not rarity:
                for pat in cls.RARITY_PATTERNS:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        rarity = strip_field_prefix(m.group(1), ["rarity", "tier", "rank", "stars", "rating"])
                        break

            # Check Event
            if not event:
                for pat in cls.EVENT_PATTERNS:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        event = strip_field_prefix(m.group(1), ["event", "edition", "version", "type", "theme"])
                        break

        # 2. Key-Value Fallback (Generic separator detection)
        if not name or not anime:
            for line in lines:
                if any(delim in line for delim in [':', '：', '-', '—', '=']):
                    parts = re.split(r'[:：\-—=]', line, maxsplit=1)
                    if len(parts) == 2:
                        k = clean_text(parts[0]).lower()
                        v = clean_text(parts[1])
                        if not v:
                            continue
                        if not name and any(x in k for x in ['name', 'char', 'waifu', '👤', '🌸']):
                            name = strip_field_prefix(v, ["name", "character", "waifu"])
                        elif not anime and any(x in k for x in ['anime', 'source', 'from', 'series', '📺', '🎬']):
                            anime = strip_field_prefix(v, ["anime", "source", "from", "series"])
                        elif not rarity and any(x in k for x in ['rarity', 'tier', 'rank', '👑', '⭐', '💎']):
                            rarity = strip_field_prefix(v, ["rarity", "tier", "rank"])
                        elif not character_id and any(x in k for x in ['id', 'code', '🆔', '🔢']):
                            character_id = strip_field_prefix(v, ["id", "code"])
                        else:
                            extra_info[k] = v

        # 3. Structural Bracket Fallback (e.g. [Anime Title] Character Name)
        if not name and lines:
            first_line = lines[0]
            bracket_match = re.search(r'[\[\(\{](.+?)[\]\)\}](.+)', first_line)
            if bracket_match:
                anime_cand = clean_text(bracket_match.group(1))
                name_cand = clean_text(bracket_match.group(2))
                if not anime:
                    anime = anime_cand
                if not name:
                    name = name_cand

        # 4. Clean Fallbacks & Defaults
        if not name:
            if lines and not any(k in lines[0].lower() for k in ['added', 'update', 'database', 'channel']):
                name = clean_text(lines[0])
            else:
                name = "Unknown"

        if not anime:
            anime = "Unknown"

        # Final cleanup pass
        name = strip_field_prefix(name, ["name", "character", "waifu", "char"]) or "Unknown"
        anime = strip_field_prefix(anime, ["anime", "source", "from", "series", "origin"]) or "Unknown"

        return {
            "name": name,
            "anime": anime,
            "rarity": rarity,
            "character_id": character_id,
            "event": event,
            "extra_info": extra_info,
            "raw_text": normalized_raw
        }
