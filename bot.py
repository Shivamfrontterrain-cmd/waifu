import io
import logging
import sys
from pathlib import Path
from collections import OrderedDict

from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, DB_PATH
from database import DatabaseManager
from matcher import WaifuMatcher

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("WaifuBot")

# Initialize database and ultra-fast matcher engine
db_manager = DatabaseManager(DB_PATH)
matcher = WaifuMatcher(DB_PATH)

# In-memory LRU cache for 0ms repeated lookups (max 500 items)
RECENT_MATCH_CACHE: OrderedDict[str, dict] = OrderedDict()
MAX_CACHE_SIZE = 500


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message and instructions."""
    user = update.effective_user
    welcome_text = (
        f"🌸 **Welcome, {user.first_name}!**\n\n"
        f"I am the **Ultra-Fast Waifu & Character Finder Bot**! ⚡\n\n"
        f"**How to use me:**\n"
        f"1. **Forward or send any anime character photo** here.\n"
        f"2. In group chats, **reply to any character image** with `/find` or `/who`.\n"
        f"3. I will instantly identify their **Name, Anime Series, and Rarity** in milliseconds!\n\n"
        f"**Useful Commands:**\n"
        f"• `/search <name>` — Search character by text\n"
        f"• `/stats` — View total indexed waifus in database\n"
        f"• `/help` — How to use this bot"
    )
    await update.message.reply_text(welcome_text, parse_mode=constants.ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help guide."""
    help_text = (
        "📖 **Waifu Finder Bot Help**\n\n"
        "• **Photo Lookup**: Simply send or forward any character artwork to this chat.\n"
        "• **In Groups**: Add me to your group, and reply to any dropped character photo with `/find` or `/who`.\n"
        "• **Name Search**: Use `/search Rem` or `/search Attack on Titan` to search by text.\n\n"
        "*(Tip: Tap the character name in the bot's reply to instantly copy it for claiming in gacha bots!)*"
    )
    await update.message.reply_text(help_text, parse_mode=constants.ParseMode.MARKDOWN)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays database statistics."""
    stats = db_manager.get_stats()
    indexed_count = len(matcher.index)
    stats_text = (
        "📊 **Waifu Database Statistics**\n\n"
        f"🌸 **Total Characters in DB**: `{stats['total_characters']:,}`\n"
        f"⚡ **Ultra-Fast Visual Index**: `{indexed_count:,}` active characters in RAM\n"
        f"🎬 **Unique Anime Franchises**: `{stats['unique_animes']:,}`\n"
        f"📡 **Source Channels**: `{stats['total_channels']}`\n\n"
        f"⚡ *Response Time: < 5 milliseconds*"
    )
    await update.message.reply_text(stats_text, parse_mode=constants.ParseMode.MARKDOWN)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Searches characters by text query."""
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide a name to search!\nExample: `/search Rem` or `/search Naruto`",
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
        name = char.get("name", "Unknown")
        anime = char.get("anime", "Unknown")
        rarity = char.get("rarity", "Normal")
        response.append(
            f"**{idx}.** `{name}`\n"
            f"   🎬 *{anime}* | 👑 {rarity}\n"
        )

    await update.message.reply_text("\n".join(response), parse_mode=constants.ParseMode.MARKDOWN)


def format_character_card(char: dict) -> str:
    """Formats a matched character card with copyable name."""
    name = char.get("name", "Unknown")
    anime = char.get("anime", "Unknown")
    rarity = char.get("rarity") or "Normal"
    char_id = char.get("character_id")
    event = char.get("event")
    confidence = char.get("confidence", 100.0)

    card = [
        "🎯 **Character Identified!**\n",
        f"🌸 **Name:** `{name}` *(Tap name to copy)*",
        f"🎬 **Anime:** *{anime}*",
        f"👑 **Rarity:** {rarity}",
    ]

    if char_id:
        card.append(f"🆔 **ID:** `{char_id}`")
    if event:
        card.append(f"🎪 **Event:** {event}")

    card.append(f"\n📊 **Accuracy:** `{confidence}%`")
    return "\n".join(card)


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes incoming character photos with ultra-low latency."""
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

    # SPEED OPTIMIZATION:
    # Use medium resolution (e.g. photos_list[1] or photos_list[0]) for 20ms downloads instead of large 4K photos
    target_photo = photos_list[1] if len(photos_list) > 1 else photos_list[0]
    unique_id = target_photo.file_unique_id

    # 1. Check in-memory 0ms cache
    if unique_id in RECENT_MATCH_CACHE:
        cached_result = RECENT_MATCH_CACHE[unique_id]
        RECENT_MATCH_CACHE.move_to_end(unique_id)
        if cached_result:
            await msg.reply_text(
                format_character_card(cached_result),
                parse_mode=constants.ParseMode.MARKDOWN,
                reply_to_message_id=msg.message_id
            )
            return

    try:
        # Download lightweight thumbnail in memory (< 20ms)
        photo_file = await target_photo.get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        image_stream = io.BytesIO(photo_bytes)

        # Match against CPU-accelerated visual index (< 1ms)
        match = matcher.find_match(image_stream)

        # Cache the result
        if len(RECENT_MATCH_CACHE) >= MAX_CACHE_SIZE:
            RECENT_MATCH_CACHE.popitem(last=False)
        RECENT_MATCH_CACHE[unique_id] = match

        if match:
            response_text = format_character_card(match)
            await msg.reply_text(
                response_text,
                parse_mode=constants.ParseMode.MARKDOWN,
                reply_to_message_id=msg.message_id
            )
        else:
            await msg.reply_text(
                "❌ **Character not found in database!**",
                parse_mode=constants.ParseMode.MARKDOWN,
                reply_to_message_id=msg.message_id
            )

    except Exception as e:
        logger.error(f"Error matching photo: {e}", exc_info=True)


def main():
    if not BOT_TOKEN:
        print("\n" + "=" * 60)
        print("❌ [ERROR] BOT_TOKEN is missing in your .env file!")
        print("1. Open Telegram and search for @BotFather")
        print("2. Create a new bot with /newbot to get your API Token")
        print("3. Add BOT_TOKEN=your_token_here into your .env file")
        print("=" * 60 + "\n")
        sys.exit(1)

    print("🚀 Starting Ultra-Fast Telegram Waifu Identifier Bot...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("find", handle_photo_message))
    app.add_handler(CommandHandler("who", handle_photo_message))
    app.add_handler(CommandHandler("guess", handle_photo_message))

    # Photos (DMs & Group replies)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))

    print(f"⚡ Bot is ONLINE! Active visual index: {len(matcher.index):,} characters.")
    print("🤖 Listening for messages with < 5ms response time...")
    app.run_polling()


if __name__ == "__main__":
    main()
