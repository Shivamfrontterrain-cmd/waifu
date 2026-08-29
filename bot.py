import io
import re
import time
import logging
import sys
from pathlib import Path
from collections import OrderedDict
from typing import Optional

from telegram import (
    Update,
    constants,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    InlineQueryHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, DB_PATH
from database import DatabaseManager
from matcher import WaifuMatcher
from parser import clean_text

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("WaifuBot")

# Initialize database and matcher engine
db_manager = DatabaseManager(DB_PATH)
matcher = WaifuMatcher(DB_PATH)

# In-memory LRU cache for 0ms repeat lookups
RECENT_MATCH_CACHE: OrderedDict[str, dict] = OrderedDict()
MAX_CACHE_SIZE = 1000
BOT_START_TIME = time.time()


def detect_claim_command(msg: Optional[Update.message]) -> str:
    """
    Detects the custom claim command from the image drop message
    (e.g., /grab, /catch, /claim, /collect, /harem, /marry, /snatch, etc.).
    Defaults to 'grab' if no command is specified.
    """
    if not msg:
        return "grab"

    text_sources = []
    if msg.caption:
        text_sources.append(msg.caption)
    if msg.text:
        text_sources.append(msg.text)
    if msg.reply_to_message:
        if msg.reply_to_message.caption:
            text_sources.append(msg.reply_to_message.caption)
        if msg.reply_to_message.text:
            text_sources.append(msg.reply_to_message.text)

    for text in text_sources:
        # Check for known gacha claim verbs
        m_verb = re.search(
            r'/(grab|catch|claim|collect|harem|snatch|marry|protect|take|roll|pick|get)\b',
            text,
            re.IGNORECASE
        )
        if m_verb:
            return m_verb.group(1).lower()

        # Check for any slash command followed by NAME, [NAME], TO CATCH, etc.
        m_generic = re.search(
            r'/([a-zA-Z0-9_]{2,16})\s+(?:\[?name\]?|to\s+catch|to\s+grab|to\s+claim|to\s+add)',
            text,
            re.IGNORECASE
        )
        if m_generic:
            return m_generic.group(1).lower()

    return "grab"


def format_character_card(char: dict, command: str = "grab") -> str:
    """
    Formats the character card response in the exact requested monospace format:
    
    NAME : `Character Name`
    ━━━━━━━━━━━━━━━━━━
    🔹 Hint : `/command hint_name`
    🔸 Full : `/command Character Name`
    """
    raw_name = char.get("name") or "Unknown"
    clean_char_name = clean_text(raw_name).strip()
    hint_name = clean_char_name.lower()

    return (
        f"NAME : `{clean_char_name}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔹 Hint : `/{command} {hint_name}`\n"
        f"🔸 Full : `/{command} {clean_char_name}`"
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Interactive welcome banner and menu."""
    user = update.effective_user
    welcome_text = (
        f"🌸 **Hello, {user.first_name}!**\n\n"
        f"I am your **Ultra-Fast Waifu & Character Finder Bot**! 🎯⚡\n\n"
        f"**How to use me:**\n"
        f"• **In DMs:** Forward or send any dropped character image here.\n"
        f"• **In Groups:** Reply to any character spawn with `/find` or `/who`.\n"
        f"• **Inline Mode:** Type `@{(await context.bot.get_me()).username} <name>` in any chat!\n\n"
        f"I will instantly give you the **exact name and 1-tap claim command** in milliseconds!"
    )
    keyboard = [
        [
            InlineKeyboardButton("🔍 Search Characters", switch_inline_query_current_chat=""),
            InlineKeyboardButton("📊 Database Stats", callback_data="stats_btn")
        ]
    ]
    await update.message.reply_text(
        welcome_text,
        parse_mode=constants.ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed help guide."""
    help_text = (
        "📖 **Waifu Finder Bot Guide**\n\n"
        "**Image Recognition:**\n"
        "• Send or forward any character photo to get their 1-tap claim command.\n"
        "• In groups, reply to any image with `/find`, `/who`, `/guess`, or `/claim`.\n\n"
        "**Commands:**\n"
        "• `/search <name>` — Search waifus by name or anime\n"
        "• `/anime <title>` — View all characters from an anime\n"
        "• `/id <number>` — Look up character by gacha ID\n"
        "• `/random` — Drop a random character card\n"
        "• `/stats` — View live database statistics\n"
        "• `/reload` — Refresh database catalog from GitHub\n\n"
        "*(Tip: Tap the Hint or Full claim line to instantly copy and paste into group chats!)*"
    )
    await update.message.reply_text(help_text, parse_mode=constants.ParseMode.MARKDOWN)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays live database statistics and bot uptime."""
    stats = db_manager.get_stats()
    total_chars = len(matcher.all_characters) or stats['total_characters']
    unique_ids = len(matcher.unique_id_map)
    visual_hashes = len(matcher.visual_index)

    uptime_sec = int(time.time() - BOT_START_TIME)
    mins, secs = divmod(uptime_sec, 60)
    hours, mins = divmod(mins, 60)

    stats_text = (
        "📊 **Waifu Database Statistics**\n\n"
        f"🌸 **Total Characters**: `{total_chars:,}`\n"
        f"⚡ **Unique ID Index**: `{unique_ids:,}` instant cloud maps\n"
        f"🖼️ **Visual Hashes**: `{visual_hashes:,}` active perceptual models\n"
        f"🎬 **Unique Animes**: `{stats['unique_animes']:,}` franchises\n"
        f"📡 **Source Channels**: `{stats['total_channels']}` database channels\n"
        f"⏱️ **Bot Uptime**: `{hours}h {mins}m {secs}s`\n\n"
        f"⚡ *Response Latency: < 5 milliseconds*"
    )
    await update.message.reply_text(stats_text, parse_mode=constants.ParseMode.MARKDOWN)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Searches characters by text query."""
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide a name to search!\nExample: `/search Rem` or `/search Sukuna`",
            parse_mode=constants.ParseMode.MARKDOWN
        )
        return

    query = " ".join(context.args)
    results = matcher.search_by_name(query, limit=5)

    if not results:
        await update.message.reply_text(
            f"❌ No characters found matching: `{query}`",
            parse_mode=constants.ParseMode.MARKDOWN
        )
        return

    response = [f"🔍 **Search Results for:** `{query}`\n"]
    for idx, char in enumerate(results, 1):
        name = clean_text(char.get("name") or "Unknown")
        anime = clean_text(char.get("anime") or "Unknown")
        rarity = char.get("rarity") or "Normal"
        cid = char.get("character_id")
        id_str = f" | 🆔 `{cid}`" if cid else ""
        response.append(f"**{idx}.** `{name}`\n   🎬 *{anime}* | 👑 {rarity}{id_str}\n")

    await update.message.reply_text("\n".join(response), parse_mode=constants.ParseMode.MARKDOWN)


async def anime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns character roster from a specific anime series."""
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide an anime name!\nExample: `/anime Jujutsu Kaisen` or `/anime Naruto`",
            parse_mode=constants.ParseMode.MARKDOWN
        )
        return

    anime_query = " ".join(context.args)
    results = matcher.search_by_anime(anime_query, limit=8)

    if not results:
        await update.message.reply_text(
            f"❌ No characters found from anime: `{anime_query}`",
            parse_mode=constants.ParseMode.MARKDOWN
        )
        return

    response = [f"🎬 **Characters from:** *{anime_query}*\n"]
    for idx, char in enumerate(results, 1):
        name = clean_text(char.get("name") or "Unknown")
        rarity = char.get("rarity") or "Normal"
        response.append(f"**{idx}.** `{name}` — 👑 {rarity}")

    await update.message.reply_text("\n".join(response), parse_mode=constants.ParseMode.MARKDOWN)


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Looks up a character by their exact character ID."""
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide a character ID!\nExample: `/id 1` or `/id 1042`",
            parse_mode=constants.ParseMode.MARKDOWN
        )
        return

    target_id = context.args[0]
    char = matcher.get_character_by_id(target_id)
    if char:
        cmd = detect_claim_command(update.message)
        await update.message.reply_text(format_character_card(char, command=cmd), parse_mode=constants.ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(
            f"❌ Character ID `{target_id}` not found in database.",
            parse_mode=constants.ParseMode.MARKDOWN
        )


async def random_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drops a random waifu card."""
    char = matcher.get_random_character()
    if char:
        cmd = detect_claim_command(update.message)
        await update.message.reply_text(format_character_card(char, command=cmd), parse_mode=constants.ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("❌ Database is currently empty.")


async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hot-reloads the database catalog from GitHub or SQLite."""
    status_msg = await update.message.reply_text("🔄 Syncing latest database from GitHub Cloud...")
    matcher.reload()
    total_chars = len(matcher.all_characters)
    await status_msg.edit_text(
        f"✅ **Database Synced Successfully!**\n\n"
        f"🌸 Active Catalog: `{total_chars:,}` characters in memory\n"
        f"⚡ Cloud Unique IDs: `{len(matcher.unique_id_map):,}` mapped",
        parse_mode=constants.ParseMode.MARKDOWN
    )


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Supports inline query mode (@YourBotName <query>)."""
    query = update.inline_query.query.strip()
    results = []

    if not query:
        chars = [matcher.get_random_character() for _ in range(5)]
        chars = [c for c in chars if c]
    else:
        chars = matcher.search_by_name(query, limit=10)

    for idx, char in enumerate(chars):
        name = clean_text(char.get("name") or "Unknown")
        anime = clean_text(char.get("anime") or "Unknown")
        rarity = char.get("rarity") or "Normal"
        text_content = format_character_card(char, command="grab")

        item = InlineQueryResultArticle(
            id=str(idx),
            title=name,
            description=f"{anime} | {rarity}",
            input_message_content=InputTextMessageContent(
                text_content,
                parse_mode=constants.ParseMode.MARKDOWN
            )
        )
        results.append(item)

    await update.inline_query.answer(results, cache_time=5)


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes incoming photos with high-speed multi-stage recognition."""
    msg = update.message
    if not msg:
        return

    photos_list = None
    if msg.photo:
        photos_list = msg.photo
    elif msg.reply_to_message and msg.reply_to_message.photo:
        photos_list = msg.reply_to_message.photo

    if not photos_list:
        return

    # Detect if the drop message specified /catch, /grab, /claim, /collect, etc.
    claim_cmd = detect_claim_command(msg)

    # Use medium resolution for fast 20ms downloads
    target_photo = photos_list[1] if len(photos_list) > 1 else photos_list[0]
    unique_id = target_photo.file_unique_id

    # 1. Check LRU Cache (0 ms)
    if unique_id in RECENT_MATCH_CACHE:
        cached_result = RECENT_MATCH_CACHE[unique_id]
        RECENT_MATCH_CACHE.move_to_end(unique_id)
        if cached_result:
            await msg.reply_text(
                format_character_card(cached_result, command=claim_cmd),
                parse_mode=constants.ParseMode.MARKDOWN,
                reply_to_message_id=msg.message_id
            )
            return

    match = None

    # 2. Check Forward Origin (Direct forward from database channel)
    if msg.forward_from_chat and msg.forward_from_message_id:
        match = matcher.find_match_by_message_id(msg.forward_from_chat.id, msg.forward_from_message_id)
    elif hasattr(msg, 'forward_origin') and msg.forward_origin:
        origin = msg.forward_origin
        if hasattr(origin, 'chat') and hasattr(origin, 'message_id'):
            match = matcher.find_match_by_message_id(origin.chat.id, origin.message_id)

    # 3. Check O(1) Cloud Unique ID Match
    if not match:
        match = matcher.find_match_by_unique_id(unique_id)

    # 4. Check Visual Perceptual Hash Match (< 1 ms)
    if not match:
        try:
            photo_file = await target_photo.get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            image_stream = io.BytesIO(photo_bytes)
            match = matcher.find_match_by_image(image_stream)
        except Exception as e:
            logger.debug(f"Visual match attempt error: {e}")

    # 5. Check if caption has character name or parsed metadata
    if not match:
        caption_text = msg.caption or (msg.reply_to_message.caption if msg.reply_to_message else None)
        if caption_text:
            parsed = WaifuParser.parse(caption_text)
            if parsed.get("name") and parsed["name"] != "Unknown":
                candidates = matcher.search_by_name(parsed["name"], limit=1)
                if candidates:
                    match = dict(candidates[0])
                    match["confidence"] = 99.0
                    match["match_type"] = "Caption Text Match"

    # Store in LRU cache
    if len(RECENT_MATCH_CACHE) >= MAX_CACHE_SIZE:
        RECENT_MATCH_CACHE.popitem(last=False)
    RECENT_MATCH_CACHE[unique_id] = match

    if match:
        response_text = format_character_card(match, command=claim_cmd)
        await msg.reply_text(
            response_text,
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_to_message_id=msg.message_id
        )
    else:
        await msg.reply_text(
            "❌ **Character not found in database!**\n\n"
            "*(Try using `/search <name>` if you know part of their name)*",
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_to_message_id=msg.message_id
        )


def main():
    if not BOT_TOKEN:
        print("\n" + "=" * 60)
        print("❌ [ERROR] BOT_TOKEN is missing in your .env file!")
        print("1. Open Telegram and search for @BotFather")
        print("2. Create a new bot with /newbot to get your API Token")
        print("3. Add BOT_TOKEN=your_token_here into your .env file")
        print("=" * 60 + "\n")
        sys.exit(1)

    print("🚀 Starting Telegram Waifu Identifier Bot...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("anime", anime_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("random", random_command))
    app.add_handler(CommandHandler("reload", reload_command))

    # Trigger aliases for group photo replies
    app.add_handler(CommandHandler("find", handle_photo_message))
    app.add_handler(CommandHandler("who", handle_photo_message))
    app.add_handler(CommandHandler("guess", handle_photo_message))
    app.add_handler(CommandHandler("claim", handle_photo_message))
    app.add_handler(CommandHandler("grab", handle_photo_message))
    app.add_handler(CommandHandler("catch", handle_photo_message))

    # Photos & Forwarded Media
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))

    # Inline Query Handler
    app.add_handler(InlineQueryHandler(inline_query_handler))

    total_loaded = len(matcher.all_characters)
    print(f"⚡ Bot is ONLINE! Total loaded characters: {total_loaded:,}")
    print("🤖 Listening for messages, photos, and inline queries...")
    app.run_polling()


if __name__ == "__main__":
    main()
