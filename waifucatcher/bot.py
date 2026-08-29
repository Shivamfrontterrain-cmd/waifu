import io
import re
import sys
import logging
from pathlib import Path
from typing import Optional

from telegram import (
    Update,
    constants,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Project paths
CATCHER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CATCHER_DIR.parent
sys.path.insert(0, str(CATCHER_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

from game_config import (
    BOT_TOKEN,
    GAME_DB_PATH,
    CHARACTER_DB_PATH,
    ROLL_COST_COINS,
    CLAIM_REWARD_COINS,
    DAILY_COIN_REWARD,
    validate_catcher_config
)
from gamedb import GameDatabaseManager
from engine import WaifuGameEngine
from parser import sanitize_character_name, clean_text

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("WaifuCatcherBot")

# Initialize Game Database & Engine
db = GameDatabaseManager(GAME_DB_PATH)
engine = WaifuGameEngine(CHARACTER_DB_PATH)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_sender_info(update: Update):
    """Extracts user_id, username, first_name and initializes player profile."""
    user = update.effective_user
    return db.get_or_create_user(user.id, user.username, user.first_name)


async def send_character_photo(context: ContextTypes.DEFAULT_TYPE, chat_id: int, char: dict, caption: str, reply_to: Optional[int] = None, reply_markup=None):
    """
    Sends character photo instantly via Telegram Cloud (0 local disk storage used).
    1. First checks for cached telegram_file_id
    2. If not cached, streams thumbnail from Telegram Cloud into RAM, sends it, and caches file_id!
    3. Falls back to text only if character has no media.
    """
    file_id = char.get("telegram_file_id")

    if file_id:
        try:
            return await context.bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=caption,
                parse_mode=constants.ParseMode.MARKDOWN,
                reply_to_message_id=reply_to,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.debug(f"Could not send via file_id ({e}), fetching fresh cloud stream...")

    # Stream photo directly from Telegram Cloud in RAM
    img_bytes = await engine.get_character_photo_bytes(char)
    if img_bytes:
        try:
            sent_msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=img_bytes,
                caption=caption,
                parse_mode=constants.ParseMode.MARKDOWN,
                reply_to_message_id=reply_to,
                reply_markup=reply_markup
            )
            # Cache the newly generated Bot API file_id for instant subsequent sends!
            if sent_msg and sent_msg.photo:
                new_file_id = sent_msg.photo[-1].file_id
                char["telegram_file_id"] = new_file_id
                # Update shared database in background
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE characters SET telegram_file_id = ? WHERE id = ?", (new_file_id, char["id"]))
                    conn.commit()
            return sent_msg
        except Exception as e:
            logger.debug(f"Error sending cloud photo stream: {e}")

    # Fallback to rich text card if message has no photo
    return await context.bot.send_message(
        chat_id=chat_id,
        text=caption,
        parse_mode=constants.ParseMode.MARKDOWN,
        reply_to_message_id=reply_to,
        reply_markup=reply_markup
    )


# ============================================================================
# COMMAND HANDLERS
# ============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome greeting, player registration, and main menu."""
    user_data = get_sender_info(update)
    user = update.effective_user

    text = (
        f"🌸 **Welcome to Waifu Catcher Gacha, {user.first_name}!** 🌸\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Collect, summon, and trade over **192,000+ anime characters**!\n\n"
        "💰 **Starter Balance:** `250 Coins`\n"
        "🎁 **Daily Reward:** `/daily` (+500 Coins)\n"
        "🎰 **Gacha Summon:** `/roll` (100 Coins)\n"
        "🎒 **My Harem:** `/harem`\n\n"
        "💡 **How to play in Groups:**\n"
        "Add me to your group chat! Wild characters spawn as members chat. "
        "Type `/catch <name>` first to claim them into your harem!"
    )
    keyboard = [
        [
            InlineKeyboardButton("🎰 Summon Gacha (/roll)", callback_data="btn_roll"),
            InlineKeyboardButton("🎒 My Harem", callback_data="btn_harem")
        ],
        [
            InlineKeyboardButton("🎁 Claim Daily Coins", callback_data="btn_daily"),
            InlineKeyboardButton("🏆 Top Collectors", callback_data="btn_leaderboard")
        ]
    ]
    await update.message.reply_text(
        text,
        parse_mode=constants.ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the comprehensive game command guide."""
    help_text = (
        "📖 **Waifu Catcher Game Guide**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎮 **Catching & Spawning:**\n"
        "• `/catch <name>` or `/grab <name>` — Catch active wild waifu\n"
        "• `/spawn` or `/drop` — Spawn a wild character in group\n"
        "• `/testspawn` — Test spawn with answer spoiler (works in DMs & groups)\n"
        "• `/testspawn <name/id>` — Test spawn specific character (e.g. `/testspawn Gojo`)\n"
        "• `/spawndebug` — Inspect active spawn state\n"
        "• `/clearspawn` — Clear active spawn immediately\n\n"
        "🎰 **Gacha & Collection:**\n"
        "• `/roll` or `/gacha` — Summon a random waifu (100 Coins)\n"
        "• `/harem` or `/collection` — Browse your collected waifus\n"
        "• `/fav <id>` — Set your featured profile favorite\n"
        "• `/info <id>` — View full card info and photo\n\n"
        "💰 **Economy & Social:**\n"
        "• `/daily` — Claim 500 daily coins\n"
        "• `/balance` or `/bal` — Check wallet balance\n"
        "• `/profile` or `/me` — View player profile & rank\n"
        "• `/pay <@user> <amount>` — Send coins to a friend\n"
        "• `/gift <@user> <card_id>` — Gift a waifu to another player\n"
        "• `/top` or `/leaderboard` — View top collectors & richest players\n\n"
        "⚙️ **Group Admin Commands:**\n"
        "• `/setinterval <count>` — Set messages needed per spawn (default: 30)\n"
        "• `/toggle` — Enable or disable wild spawns in chat"
    )
    await update.message.reply_text(help_text, parse_mode=constants.ParseMode.MARKDOWN)


# ---------------- SPAWN & CATCH ---------------- #

async def spawn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually spawns a wild waifu in the chat."""
    chat = update.effective_chat
    char = engine.get_random_spawn()
    if not char:
        await update.message.reply_text("❌ Character catalog is empty.")
        return

    caption = engine.format_wild_spawn_caption(char)
    spawn_msg = await send_character_photo(context, chat.id, char, caption)

    if spawn_msg:
        db.set_active_spawn(chat.id, char["id"], spawn_msg.message_id)


async def testspawn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Spawns a character specifically for testing game mechanics.
    Usage:
    • /testspawn — Spawns a random character
    • /testspawn <id> — Spawns specific character by ID (e.g. /testspawn 42)
    • /testspawn <name> — Spawns specific character by name (e.g. /testspawn Gojo)
    """
    chat = update.effective_chat
    query = " ".join(context.args).strip() if context.args else None

    char = None
    if query:
        if query.isdigit():
            char = engine.get_character(int(query))
        else:
            results = engine.matcher.search_by_name(query, limit=1)
            if results:
                char = results[0]

    if not char:
        char = engine.get_random_spawn()

    if not char:
        await update.message.reply_text("❌ No characters available in database.")
        return

    name = sanitize_character_name(char.get("name") or "Unknown")
    anime = clean_text(char.get("anime") or "Unknown")
    rarity, color, _ = engine.get_rarity_info(char.get("rarity"))
    cid = char.get("character_id") or char.get("id")

    test_caption = (
        "🧪 **[SPAWN TEST MODE ACTIVE]** 🧪\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🌸 **A WILD WAIFU APPEARED!** 🌸\n"
        f"🎬 **Anime:** *{anime}*\n"
        f"👑 **Rarity:** {color} **{rarity}**\n"
        f"🆔 **Card ID:** `#{cid}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 **Answer Spoiler:** ||`{name}`||\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💬 **Test claiming now:**\n"
        f"• Type: `/catch {name}` (or `/grab`, `/claim`)\n"
        "• Or send a screenshot/photo to test visual matching!"
    )

    spawn_msg = await send_character_photo(context, chat.id, char, test_caption)
    if spawn_msg:
        db.set_active_spawn(chat.id, char["id"], spawn_msg.message_id)


async def spawndebug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays active spawn debug info for the current chat."""
    chat = update.effective_chat
    active_spawn = db.get_active_spawn(chat.id)
    if not active_spawn:
        await update.message.reply_text(
            "ℹ️ **No active spawn in this chat.**\nUse `/spawn` or `/testspawn` to create one.",
            parse_mode=constants.ParseMode.MARKDOWN
        )
        return

    char_id, msg_id = active_spawn
    char = engine.get_character(char_id)
    if char:
        name = sanitize_character_name(char.get("name") or "Unknown")
        anime = clean_text(char.get("anime") or "Unknown")
        has_phash = bool(char.get("image_phash"))
        has_file_id = bool(char.get("telegram_file_id"))

        await update.message.reply_text(
            f"🎯 **Active Spawn Debug:**\n"
            f"• **Card ID:** `#{char_id}`\n"
            f"• **Character:** ||`{name}`||\n"
            f"• **Anime:** *{anime}*\n"
            f"• **Spawn Msg ID:** `{msg_id}`\n"
            f"• **Visual Hash Ready:** `{'✅ Yes' if has_phash else '❌ No'}`\n"
            f"• **Cloud File ID Ready:** `{'✅ Yes' if has_file_id else '❌ No'}`",
            parse_mode=constants.ParseMode.MARKDOWN
        )


async def clearspawn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clears the active spawn for this chat."""
    chat = update.effective_chat
    db.clear_active_spawn(chat.id)
    await update.message.reply_text("🧹 **Active spawn cleared for this chat!**", parse_mode=constants.ParseMode.MARKDOWN)


async def catch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /catch <name>, /grab <name>, and /claim <name>."""
    chat = update.effective_chat
    user = update.effective_user
    get_sender_info(update)

    active_spawn = db.get_active_spawn(chat.id)
    if not active_spawn:
        await update.message.reply_text(
            "❌ **No active wild waifu to catch!**\nKeep chatting or type `/spawn` to find one!",
            parse_mode=constants.ParseMode.MARKDOWN
        )
        return

    char_id, spawn_msg_id = active_spawn
    target_char = engine.get_character(char_id)
    if not target_char:
        db.clear_active_spawn(chat.id)
        return

    # Check if guess provided via text arguments
    guess_text = " ".join(context.args) if context.args else ""

    # Check if user replied with text or photo
    if not guess_text and update.message.text:
        guess_text = update.message.text

    # Check photo guess if user sent image
    is_correct = False
    if update.message.photo:
        try:
            p = update.message.photo[-1]
            f = await p.get_file()
            buf = io.BytesIO(await f.download_as_bytearray())
            is_correct = engine.verify_image_guess(buf, target_char)
        except Exception:
            pass

    if not is_correct and guess_text:
        is_correct = engine.verify_name_guess(guess_text, target_char)

    if not is_correct:
        await update.message.reply_text(
            f"❌ **Incorrect guess, {user.first_name}!** Try again!",
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_to_message_id=update.message.message_id
        )
        return

    # User guessed correctly!
    db.clear_active_spawn(chat.id)
    count = db.add_to_inventory(user.id, target_char["id"], source="claim")
    db.update_balance(user.id, CLAIM_REWARD_COINS)

    success_text = engine.format_claim_success_message(target_char, user.first_name, count)
    await send_character_photo(
        context,
        chat.id,
        target_char,
        success_text,
        reply_to=update.message.message_id
    )


# ---------------- GACHA ROLL ---------------- #

async def roll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Executes a gacha summon roll for 100 coins."""
    user = update.effective_user
    user_data = get_sender_info(update)

    if user_data["balance"] < ROLL_COST_COINS:
        await update.message.reply_text(
            f"❌ **Insufficient Coins!**\n"
            f"You have `{user_data['balance']} Coins`, but a summon costs `{ROLL_COST_COINS} Coins`.\n"
            f"*(Claim your `/daily` or catch wild waifus in groups to earn coins!)*",
            parse_mode=constants.ParseMode.MARKDOWN
        )
        return

    # Deduct cost
    new_bal = db.update_balance(user.id, -ROLL_COST_COINS)

    # Pick random character
    char = engine.get_random_spawn()
    if not char:
        await update.message.reply_text("❌ Character database is empty.")
        return

    count = db.add_to_inventory(user.id, char["id"], source="roll")
    caption = engine.format_roll_success_message(char, user.first_name, count, new_bal)

    await send_character_photo(context, update.effective_chat.id, char, caption)


# ---------------- HAREM / COLLECTION ---------------- #

def build_harem_page(user_id: int, user_name: str, page: int = 1, per_page: int = 8):
    """Builds interactive paginated harem card."""
    inv = db.get_user_inventory(user_id)
    total_items = len(inv)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start = (page - 1) * per_page
    end = start + per_page
    page_items = inv[start:end]

    text_lines = [
        f"🎒 **{user_name}'s Harem Collection**",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🌸 **Unique Waifus:** `{total_items:,}` | 📄 **Page:** `{page}/{total_pages}`\n"
    ]

    if not page_items:
        text_lines.append("*(Your collection is currently empty! Use `/roll` or `/catch` to start!)*")
    else:
        for idx, item in enumerate(page_items, start=start + 1):
            c = engine.get_character(item["character_id"])
            if not c:
                continue
            name = sanitize_character_name(c.get("name") or "Unknown")
            anime = clean_text(c.get("anime") or "Unknown")
            rarity, color, _ = engine.get_rarity_info(c.get("rarity"))
            fav_star = " ⭐" if item.get("is_favorite") else ""
            copies = f" `x{item['count']}`" if item["count"] > 1 else ""

            text_lines.append(f"**{idx}.** {color} **`{name}`**{fav_star}{copies}\n    🎬 *{anime}* | 🆔 `#{c['id']}`")

    text_lines.append("━━━━━━━━━━━━━━━━━━━━\n*(Tap `/info <id>` to inspect a card)*")

    # Navigation buttons
    buttons = []
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀️ Previous", callback_data=f"harem_{user_id}_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"harem_{user_id}_{page + 1}"))

    if nav_row:
        buttons.append(nav_row)

    return "\n".join(text_lines), InlineKeyboardMarkup(buttons) if buttons else None


async def harem_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the player's paginated harem collection."""
    user = update.effective_user
    get_sender_info(update)

    text, markup = build_harem_page(user.id, user.first_name, page=1)
    await update.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=markup)


# ---------------- PROFILE & FAVORITE ---------------- #

async def fav_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets player's featured profile waifu: /fav <id>."""
    user = update.effective_user
    get_sender_info(update)

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "⚠️ **Usage:** `/fav <character_id>`\nExample: `/fav 42` (Find ID in `/harem`)",
            parse_mode=constants.ParseMode.MARKDOWN
        )
        return

    char_id = int(context.args[0])
    char = engine.get_character(char_id)
    if not char:
        await update.message.reply_text(f"❌ Character `#{char_id}` not found.")
        return

    success = db.set_favorite(user.id, char_id)
    if success:
        name = sanitize_character_name(char.get("name") or "Unknown")
        await update.message.reply_text(
            f"⭐ **Favorite Set!**\n**`{name}`** is now your featured profile waifu! 💖",
            parse_mode=constants.ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text("❌ You do not own this character in your harem!")


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays player profile card."""
    user = update.effective_user
    stats = db.get_user_stats(user.id)
    if not stats:
        get_sender_info(update)
        stats = db.get_user_stats(user.id)

    fav_text = "None (Set with `/fav <id>`)"
    fav_photo = None
    if stats.get("fav_character_id"):
        fav_char = engine.get_character(stats["fav_character_id"])
        if fav_char:
            name = sanitize_character_name(fav_char.get("name") or "Unknown")
            anime = clean_text(fav_char.get("anime") or "Unknown")
            fav_text = f"**`{name}`** (*{anime}*)"
            fav_photo = fav_char.get("telegram_file_id")

    profile_text = (
        f"👤 **Player Profile: {user.first_name}**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 **Global Rank:** `#{stats.get('rank', 1)}`\n"
        f"💰 **Wallet Balance:** `{stats.get('balance', 0):,} Coins`\n"
        f"🌸 **Unique Waifus:** `{stats.get('unique_characters', 0):,}`\n"
        f"🎒 **Total Cards Owned:** `{stats.get('total_cards', 0):,}`\n"
        f"🎯 **Wild Catches:** `{stats.get('total_claims', 0):,}`\n"
        f"🎰 **Gacha Summons:** `{stats.get('total_rolls', 0):,}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⭐ **Featured Waifu:**\n{fav_text}"
    )

    if fav_photo:
        await send_character_photo(context, update.effective_chat.id, {"telegram_file_id": fav_photo}, profile_text)
    else:
        await update.message.reply_text(profile_text, parse_mode=constants.ParseMode.MARKDOWN)


# ---------------- ECONOMY & SOCIAL ---------------- #

async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claims daily login reward."""
    user = update.effective_user
    get_sender_info(update)

    success, coins, time_left = db.claim_daily(user.id)
    if success:
        user_data = db.get_or_create_user(user.id, user.username, user.first_name)
        await update.message.reply_text(
            f"🎁 **Daily Reward Claimed!**\n"
            f"You received `+{coins} Coins`!\n"
            f"💰 **New Balance:** `{user_data['balance']:,} Coins`\n"
            f"*(Come back tomorrow for more!)*",
            parse_mode=constants.ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            f"⏳ **Daily Reward Cooldown!**\nPlease wait `{time_left}` before claiming again.",
            parse_mode=constants.ParseMode.MARKDOWN
        )


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Checks user coin balance."""
    user_data = get_sender_info(update)
    await update.message.reply_text(
        f"💰 **Wallet Balance:** `{user_data['balance']:,} Coins`\n"
        f"Use `/roll` to summon waifus (100 coins) or `/daily` for free coins!",
        parse_mode=constants.ParseMode.MARKDOWN
    )


async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends coins to another player: /pay <amount> (in reply to their message)."""
    sender = update.effective_user
    sender_data = get_sender_info(update)

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ **Usage:** Reply to someone's message with `/pay <amount>`")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ Please specify an amount: `/pay 100`")
        return

    amount = int(context.args[0])
    if amount <= 0:
        await update.message.reply_text("❌ Amount must be greater than 0.")
        return

    if sender_data["balance"] < amount:
        await update.message.reply_text(f"❌ You only have `{sender_data['balance']} Coins`.")
        return

    target = update.message.reply_to_message.from_user
    if target.id == sender.id or target.is_bot:
        await update.message.reply_text("❌ Invalid recipient.")
        return

    db.get_or_create_user(target.id, target.username, target.first_name)
    db.update_balance(sender.id, -amount)
    db.update_balance(target.id, amount)

    await update.message.reply_text(
        f"💸 **Payment Successful!**\n"
        f"{sender.first_name} sent `{amount:,} Coins` to {target.first_name}!",
        parse_mode=constants.ParseMode.MARKDOWN
    )


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inspects a character card by ID: /info <id>."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ **Usage:** `/info <character_id>`\nExample: `/info 10`")
        return

    char_id = int(context.args[0])
    char = engine.get_character(char_id)
    if not char:
        await update.message.reply_text(f"❌ Character `#{char_id}` not found.")
        return

    name = sanitize_character_name(char.get("name") or "Unknown")
    anime = clean_text(char.get("anime") or "Unknown")
    rarity, color, price = engine.get_rarity_info(char.get("rarity"))

    card_text = (
        f"🌸 **Character Info: `{name}`**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 **Rarity:** {color} **{rarity}**\n"
        f"🎬 **Series:** *{anime}*\n"
        f"🆔 **Card ID:** `#{char['id']}`\n"
        f"💎 **Market Value:** `{price} Coins`\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await send_character_photo(context, update.effective_chat.id, char, card_text)


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays top collectors and wealthiest players."""
    top_catchers = db.get_top_collectors(limit=10)

    lines = [
        "🏆 **WAIFU CATCHER LEADERBOARD** 🏆",
        "━━━━━━━━━━━━━━━━━━━━",
        "👑 **Top Collectors (Wild Catches):**\n"
    ]

    if not top_catchers:
        lines.append("*(No players on the leaderboard yet!)*")
    else:
        for idx, p in enumerate(top_catchers, 1):
            name = p["first_name"] or (f"@{p['username']}" if p.get("username") else f"User {p['user_id']}")
            medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"**{idx}.**"
            lines.append(f"{medal} {name} — 🎯 `{p['total_claims']:,}` Catches (`{p['unique_chars']:,}` waifus)")

    await update.message.reply_text("\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN)


# ---------------- GROUP ADMIN SETTINGS ---------------- #

async def set_interval_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets chat spawn frequency: /setinterval <count>."""
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text("❌ This command can only be used in group chats.")
        return

    user = update.effective_user
    member = await chat.get_member(user.id)
    if member.status not in ("creator", "administrator"):
        await update.message.reply_text("❌ Only group administrators can configure spawn intervals.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ **Usage:** `/setinterval <number>`\nExample: `/setinterval 25` (messages)")
        return

    interval = int(context.args[0])
    db.set_chat_interval(chat.id, interval)
    await update.message.reply_text(
        f"✅ **Spawn Interval Updated!**\nA wild waifu will now spawn every `{interval}` messages!",
        parse_mode=constants.ParseMode.MARKDOWN
    )


async def toggle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles wild spawns on/off in group."""
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text("❌ This command is for group chats only.")
        return

    user = update.effective_user
    member = await chat.get_member(user.id)
    if member.status not in ("creator", "administrator"):
        await update.message.reply_text("❌ Only administrators can toggle spawns.")
        return

    is_on = db.toggle_chat_spawns(chat.id)
    state = "ENABLED 🟢" if is_on else "DISABLED 🔴"
    await update.message.reply_text(
        f"⚙️ Wild Waifu Spawns are now **{state}** in this group!",
        parse_mode=constants.ParseMode.MARKDOWN
    )


# ============================================================================
# BACKGROUND MESSAGE LISTENER (AUTO-SPAWN)
# ============================================================================

async def group_message_watcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Watches group chat activity and automatically triggers wild waifu spawns."""
    msg = update.message
    if not msg or not msg.text:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return

    # Check if message counter reached threshold
    should_spawn = db.register_chat_message(chat.id, chat.title or f"Chat {chat.id}")
    if should_spawn:
        char = engine.get_random_spawn()
        if char:
            caption = engine.format_wild_spawn_caption(char)
            spawn_msg = await send_character_photo(context, chat.id, char, caption)
            if spawn_msg:
                db.set_active_spawn(chat.id, char["id"], spawn_msg.message_id)


# ============================================================================
# CALLBACK HANDLER
# ============================================================================

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles inline button clicks for menu, rolls, and harem pagination."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user

    if data == "btn_roll":
        user_data = db.get_or_create_user(user.id, user.username, user.first_name)
        if user_data["balance"] < ROLL_COST_COINS:
            await query.message.reply_text(f"❌ Need `{ROLL_COST_COINS} Coins` to summon! Use `/daily`.")
            return
        new_bal = db.update_balance(user.id, -ROLL_COST_COINS)
        char = engine.get_random_spawn()
        count = db.add_to_inventory(user.id, char["id"], source="roll")
        caption = engine.format_roll_success_message(char, user.first_name, count, new_bal)
        await send_character_photo(context, query.message.chat_id, char, caption)

    elif data == "btn_harem":
        text, markup = build_harem_page(user.id, user.first_name, page=1)
        await query.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=markup)

    elif data == "btn_daily":
        success, coins, time_left = db.claim_daily(user.id)
        if success:
            await query.message.reply_text(f"🎁 Claimed `+{coins} Coins`! Use `/roll` to summon!")
        else:
            await query.message.reply_text(f"⏳ Cooldown: Wait `{time_left}` before claiming again.")

    elif data == "btn_leaderboard":
        top = db.get_top_collectors(limit=5)
        lines = ["🏆 **Top 5 Collectors:**\n"]
        for idx, p in enumerate(top, 1):
            lines.append(f"**{idx}.** {p['first_name']} — 🎯 `{p['total_claims']}` Catches")
        await query.message.reply_text("\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN)

    elif data.startswith("harem_"):
        parts = data.split("_")
        owner_id = int(parts[1])
        page = int(parts[2])
        if user.id != owner_id:
            await query.answer("⚠️ This is not your harem menu!", show_alert=True)
            return
        text, markup = build_harem_page(owner_id, user.first_name, page=page)
        try:
            await query.edit_message_text(text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=markup)
        except Exception:
            pass


# ============================================================================
# MAIN ENTRYPOINT
# ============================================================================

def main():
    errors = validate_catcher_config()
    if errors:
        print("\n" + "=" * 60)
        print("❌ [CONFIG ERROR]")
        for e in errors:
            print(f"  • {e}")
        print("=" * 60 + "\n")
        sys.exit(1)

    print("🚀 Starting Waifu Catcher Gacha Game Bot...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("spawn", spawn_command))
    app.add_handler(CommandHandler("drop", spawn_command))
    app.add_handler(CommandHandler(["testspawn", "spawntest", "forcespawn", "testdrop"], testspawn_command))
    app.add_handler(CommandHandler(["spawndebug", "spawns", "currentspawn"], spawndebug_command))
    app.add_handler(CommandHandler(["clearspawn", "despawn"], clearspawn_command))
    app.add_handler(CommandHandler(["catch", "grab", "claim"], catch_command))
    app.add_handler(CommandHandler(["roll", "gacha", "pull"], roll_command))
    app.add_handler(CommandHandler(["harem", "collection", "mywaifus"], harem_command))
    app.add_handler(CommandHandler("fav", fav_command))
    app.add_handler(CommandHandler(["profile", "me"], profile_command))
    app.add_handler(CommandHandler("daily", daily_command))
    app.add_handler(CommandHandler(["balance", "bal", "coins"], balance_command))
    app.add_handler(CommandHandler("pay", pay_command))
    app.add_handler(CommandHandler(["info", "char"], info_command))
    app.add_handler(CommandHandler(["top", "leaderboard"], leaderboard_command))
    app.add_handler(CommandHandler("setinterval", set_interval_command))
    app.add_handler(CommandHandler("toggle", toggle_command))

    # Callback Query Handler
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # Group Chat Message Counter (Auto-Spawns)
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, group_message_watcher))

    # Photo Guesses
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.GROUPS, catch_command))

    print(f"⚡ Bot is ONLINE! Ready to spawn & catch across 192,000+ characters!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
