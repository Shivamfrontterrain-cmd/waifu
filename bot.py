import io
import logging
import sys
from pathlib import Path

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

# Initialize database and matcher engine
db_manager = DatabaseManager(DB_PATH)
matcher = WaifuMatcher(DB_PATH)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message and instructions."""
    user = update.effective_user
    welcome_text = (
        f"🌸 **Welcome, {user.first_name}!**\n\n"
        f"I am the **Waifu & Character Finder Bot**! 🎯\n\n"
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
        f"🖼️ **Visual Search Index**: `{indexed_count:,}` indexed artworks\n"
        f"🎬 **Unique Anime Franchises**: `{stats['unique_animes']:,}`\n"
        f"📡 **Source Channels**: `{stats['total_channels']}`\n\n"
        f"⚡ *Powered by Perceptual Hash Visual Search*"
    )
    await update.message.reply_text(stats_text, parse_mode=constants.ParseMode.MARKDOWN)


async def reindex_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin utility to compute hashes for any new images in DB."""
    status_msg = await update.message.reply_text("🔄 Indexing character artwork into visual search engine...")
    count = matcher.build_hash_index()
    await status_msg.edit_text(
        f"✅ **Indexing Complete!**\n\n"
        f"• Newly indexed: `{count:,}` characters\n"
        f"• Total active index: `{len(matcher.index):,}` characters",
        parse_mode=constants.ParseMode.MARKDOWN
    )


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
    image_url = char.get("image_url")

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
    if image_url:
        card.append(f"🖼️ **Cloud Artwork:** [Open Image]({image_url})")

    card.append(f"\n📊 **Match Accuracy:** `{confidence}%`")
    return "\n".join(card)


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes incoming character photos and finds a match."""
    msg = update.message
    if not msg:
        return

    # Check if image is in the message itself or in a replied message
    target_photo = None
    if msg.photo:
        target_photo = msg.photo[-1]  # Highest resolution
    elif msg.reply_to_message and msg.reply_to_message.photo:
        target_photo = msg.reply_to_message.photo[-1]

    if not target_photo:
        return

    # Send typing/analyzing indicator
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=constants.ChatAction.TYPING)

    try:
        # Download photo into BytesIO in memory (0 disk storage used)
        photo_file = await target_photo.get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        image_stream = io.BytesIO(photo_bytes)

        # Match against our visual character index
        match = matcher.find_match(image_stream)

        if match:
            response_text = format_character_card(match)
            await msg.reply_text(
                response_text,
                parse_mode=constants.ParseMode.MARKDOWN,
                reply_to_message_id=msg.message_id
            )
        else:
            await msg.reply_text(
                "❌ **Character not found in database!**\n\n"
                "*(The image might not be in our current channel database yet)*",
                parse_mode=constants.ParseMode.MARKDOWN,
                reply_to_message_id=msg.message_id
            )

    except Exception as e:
        logger.error(f"Error matching photo: {e}", exc_info=True)
        await msg.reply_text("⚠️ An error occurred while identifying the image.", reply_to_message_id=msg.message_id)


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
    # Preload index
    indexed_count = len(matcher.index)
    if indexed_count == 0:
        print("⚡ Indexing database images for the first time...")
        matcher.build_hash_index()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("reindex", reindex_command))
    app.add_handler(CommandHandler("find", handle_photo_message))
    app.add_handler(CommandHandler("who", handle_photo_message))
    app.add_handler(CommandHandler("guess", handle_photo_message))

    # Photos (DMs & Group replies)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))

    print(f"✅ Bot is ONLINE! Active visual index: {len(matcher.index):,} characters.")
    print("🤖 Listening for messages...")
    app.run_polling()


if __name__ == "__main__":
    main()
