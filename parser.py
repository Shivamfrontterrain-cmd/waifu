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
        pat = rf'^(?:{fn})\s*[:：\-—=.]\s*'
        cleaned = re.sub(pat, '', cleaned, flags=re.IGNORECASE).strip()
    return cleaned.strip(' -:—=.')


def sanitize_character_name(val: str) -> str:
    """
    Cleans raw character names:
    - Strips prefixes like 'Name:', 'NAME:', '👤 Name:', 'Character:', 'Waifu:', etc.
    - Strips markdown formatting (*, _, `, ~)
    - Normalizes mathematical/script unicode fonts to ASCII
    - Strips leading/trailing emojis, brackets, quotes, colons, dashes
    """
    if not val:
        return "Unknown"
    
    cleaned = normalize_unicode_fonts(val)
    cleaned = re.sub(r'[*_`~]+', '', cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = cleaned.replace('\u200b', '').replace('\u200e', '').replace('\u200f', '').replace('\ufeff', '')
    
    # Strip leading emojis, symbols, brackets
    cleaned = re.sub(r'^[^\w\s\(\)\[\]]+', '', cleaned).strip()
    
    # Repeatedly strip known prefixes in case of stacked prefixes (e.g. "👤 Name: Name: Sukuna")
    for _ in range(3):
        cleaned = re.sub(
            r'^(?:name|character|waifu|husbando|char|hero|heroine|card|drop)\s*[:：\-—=.]\s*',
            '',
            cleaned,
            flags=re.IGNORECASE
        ).strip()
        cleaned = re.sub(r'^[^\w\s\(\)]+', '', cleaned).strip()

    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' "\'—:-=.')
    return cleaned or "Unknown"


def sanitize_filename(name: str, max_length: int = 60) -> str:
    """Sanitizes strings to be valid file names across Windows and POSIX."""
    if not name:
        return "unnamed"
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    sanitized = re.sub(r'\s+', '_', sanitized).strip('._- ')
    if not sanitized:
        return "unnamed"
    return sanitized[:max_length]


NON_CHARACTER_PHRASES = {
    "owo! add new waifu!", "add new waifu", "add new waifu!", "new waifu added", "waifu added",
    "uploaded (/li)", "photo updated", "character added", "grab your waifu", "join channel",
    "click here", "unknown", "n/a", "none", "bot restart", "uploaded", "photo update", "image updated"
}


class WaifuParser:
    """Smart parser for extracting waifu/character metadata from message captions/text."""

    # Common Key Regex Patterns (Applied after NFKD normalization)
    NAME_PATTERNS = [
        r'(?:name|character|waifu|husbando|char|hero|heroine)\s*[:：\-—=]\s*(.+)',
        r'(?:🌸|👤|💮|🎀|💖|✨|🪄|👧|👩|📛)\s*(?:name|character)?\s*[:：\-—]?\s*(.+)',
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

        # 1. Look for Number: Name lines (e.g. "3677: Firefly [🐰]" or "5530: Yelan [🔞]")
        for line in lines:
            m = re.search(r'^(?:#|id\s*)?(\d{1,7})\s*[:：\-—.]\s*(.+)$', line, re.IGNORECASE)
            if m:
                cid = m.group(1).strip()
                cname = re.sub(r'\[.*?\]|\(.*?\)', '', m.group(2)).strip()
                cname = sanitize_character_name(cname)
                if cname and cname.lower() not in NON_CHARACTER_PHRASES:
                    character_id = cid
                    name = cname
                    break

        # 2. Line-by-line regex key matching
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
                        cand = strip_field_prefix(m.group(1), ["name", "character", "waifu", "char"])
                        cand = sanitize_character_name(cand)
                        if cand and cand.lower() not in NON_CHARACTER_PHRASES:
                            name = cand
                            break

            # Check Anime
            if not anime:
                for pat in cls.ANIME_PATTERNS:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        anime = strip_field_prefix(m.group(1), ["anime", "source", "from", "series", "origin"])
                        anime = sanitize_character_name(anime)
                        break

            # Check Rarity
            if not rarity:
                for pat in cls.RARITY_PATTERNS:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        val = m.group(1).strip().rstrip(')').strip()
                        rarity = strip_field_prefix(val, ["rarity", "tier", "rank", "stars", "rating"])
                        rarity = sanitize_character_name(rarity)
                        break

            # Check Event
            if not event:
                for pat in cls.EVENT_PATTERNS:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        event = strip_field_prefix(m.group(1), ["event", "edition", "version", "type", "theme"])
                        break

        # 3. Key-Value Fallback
        if not name or not anime:
            for line in lines:
                if any(delim in line for delim in [':', '：', '-', '—', '=']):
                    parts = re.split(r'[:：\-—=]', line, maxsplit=1)
                    if len(parts) == 2:
                        k = clean_text(parts[0]).lower()
                        v = clean_text(parts[1])
                        if not v:
                            continue
                        if not name and any(x in k for x in ['name', 'char', 'waifu', '👤', '🌸', '📛']):
                            cand = strip_field_prefix(v, ["name", "character", "waifu"])
                            cand = sanitize_character_name(cand)
                            if cand and cand.lower() not in NON_CHARACTER_PHRASES:
                                name = cand
                        elif not anime and any(x in k for x in ['anime', 'source', 'from', 'series', '📺', '🎬']):
                            anime = strip_field_prefix(v, ["anime", "source", "from", "series"])
                        elif not rarity and any(x in k for x in ['rarity', 'tier', 'rank', '👑', '⭐', '💎']):
                            rarity = strip_field_prefix(v.rstrip(')'), ["rarity", "tier", "rank"])
                        elif not character_id and any(x in k for x in ['id', 'code', '🆔', '🔢']):
                            character_id = strip_field_prefix(v, ["id", "code"])
                        else:
                            extra_info[k] = v

        # 4. Standalone Anime Line Fallback (e.g. "Honkai Star Rail" or "Genshin Impact")
        if not anime:
            for line in lines:
                cleaned = clean_text(line).strip()
                if cleaned.lower() in NON_CHARACTER_PHRASES:
                    continue
                if re.search(r'^(?:#|id\s*)?\d+\s*[:：\-—.]', cleaned):
                    continue
                if any(k in cleaned.lower() for k in ['rarity', 'added by', 'uploaded', 'tier', 'rank', 'photo updated', 'uploader']):
                    continue
                if len(cleaned) >= 2 and len(cleaned) <= 40:
                    anime = cleaned
                    break

        # 5. Clean Fallbacks & Defaults
        if not name or name.lower() in NON_CHARACTER_PHRASES:
            for line in lines:
                cleaned = clean_text(line).strip()
                if cleaned.lower() not in NON_CHARACTER_PHRASES and len(cleaned) >= 2:
                    if not any(k in cleaned.lower() for k in ['added', 'update', 'database', 'channel', 'rarity', 'uploader']):
                        name = cleaned
                        break
            if not name or name.lower() in NON_CHARACTER_PHRASES:
                name = "Unknown"

        name = sanitize_character_name(name)
        anime = clean_text(anime or "Unknown")

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
