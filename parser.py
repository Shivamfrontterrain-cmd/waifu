import re
import html
from typing import Dict, Any, Optional


def clean_text(text: str) -> str:
    """Removes telegram markdown artifacts, extra spaces, and unescapes HTML."""
    if not text:
        return ""
    # Strip markdown symbols like **, __, ``, ~~
    text = re.sub(r'[*_`~]+', '', text)
    text = html.unescape(text)
    # Remove zero-width spaces & normalize spaces
    text = text.replace('\u200b', '').replace('\u200e', '').replace('\u200f', '').replace('\ufeff', '')
    return text.strip()


def sanitize_filename(name: str, max_length: int = 60) -> str:
    """Sanitizes strings to be valid file names across Windows and POSIX."""
    if not name:
        return "unnamed"
    # Remove invalid characters: < > : " / \ | ? *
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    sanitized = re.sub(r'\s+', '_', sanitized).strip('._- ')
    if not sanitized:
        return "unnamed"
    return sanitized[:max_length]


class WaifuParser:
    """Smart parser for extracting waifu/character metadata from message captions/text."""

    # Common Key Regex Patterns
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

        raw_text = raw_caption.strip()
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

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
                        name = clean_text(m.group(1))
                        break

            # Check Anime / Source
            if not anime:
                for pat in cls.ANIME_PATTERNS:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        anime = clean_text(m.group(1))
                        break

            # Check Rarity
            if not rarity:
                # Check if the line is purely stars
                pure_stars = re.fullmatch(r'\s*[⭐★✨\s]{1,10}\s*', line)
                if pure_stars:
                    star_count = len(re.findall(r'[⭐★✨]', line))
                    rarity = f"{star_count} Stars"
                else:
                    for pat in cls.RARITY_PATTERNS:
                        m = re.search(pat, line, re.IGNORECASE)
                        if m:
                            val = clean_text(m.group(1))
                            if val:
                                rarity = val
                                break

            # Check Event
            if not event:
                for pat in cls.EVENT_PATTERNS:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        event = clean_text(m.group(1))
                        break

            # Generic Key-Value fallback (e.g. "Favorites: 123", "Gender: Female")
            kv_match = re.match(r'^([A-Za-z\s]+)[:：]\s*(.+)$', cleaned_line)
            if kv_match:
                k = kv_match.group(1).strip().lower()
                v = kv_match.group(2).strip()
                if k not in ("name", "anime", "rarity", "id", "character", "source", "tier"):
                    extra_info[k] = v

        # 2. Heuristic fallback if Name is still not found
        if not name and lines:
            first_line = clean_text(lines[0])

            # Check for bracket/separator format: "Name - Anime" or "Name | Anime" or "Name (Anime)"
            sep_match = re.match(r'^(.+?)\s*[\-|–—|/]\s*(.+)$', first_line)
            paren_match = re.match(r'^(.+?)\s*\((.+?)\)$', first_line)
            bracket_match = re.match(r'^[【\[](.+?)[】\]]\s*(.*)$', first_line)

            if sep_match:
                name = clean_text(sep_match.group(1))
                if not anime:
                    anime = clean_text(sep_match.group(2))
            elif paren_match:
                name = clean_text(paren_match.group(1))
                if not anime:
                    anime = clean_text(paren_match.group(2))
            elif bracket_match:
                name = clean_text(bracket_match.group(1))
                if not anime and bracket_match.group(2):
                    anime = clean_text(bracket_match.group(2))
            else:
                # Use entire first line as character name
                name = first_line

        # 3. Heuristic fallback for Anime if second line exists and anime not found
        if not anime and len(lines) > 1 and not lines[1].startswith(('http', '#', '🆔', 'ID:')):
            second_line = clean_text(lines[1])
            # Only use if not a key-value pair of something else and not a star rating
            if not re.search(r'(rarity|tier|event|rank|id|code)[:：]', second_line, re.IGNORECASE) and not re.match(r'^[⭐★✨\s]+$', second_line):
                anime = second_line

        # Clean star counts for rarity if standalone stars found anywhere in text
        if not rarity:
            stars_match = re.search(r'[⭐★✨]{1,7}', raw_text)
            if stars_match:
                rarity = f"{len(stars_match.group(0))} Stars"

        return {
            "name": name or "Unknown",
            "anime": anime or "Unknown",
            "rarity": rarity or "Normal",
            "character_id": character_id,
            "event": event,
            "extra_info": extra_info,
            "raw_text": raw_text
        }
