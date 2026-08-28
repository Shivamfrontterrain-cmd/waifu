import os
import sys
import asyncio
import logging
from pathlib import Path
from typing import List, Optional

from telethon import TelegramClient, functions, types, errors
from tqdm import tqdm

from config import (
    API_ID,
    API_HASH,
    PHONE,
    SESSION_NAME,
    FOLDER_NAME,
    DOWNLOAD_IMAGES,
    IMAGE_DIR,
    DB_PATH,
    EXPORT_JSON_PATH,
    EXPORT_CSV_PATH,
    validate_config
)
from database import DatabaseManager
from parser import WaifuParser, sanitize_filename

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
# Silence noisy internal Telethon logs
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger("WaifuScraper")



class TelegramWaifuScraper:
    def __init__(self):
        self.db = DatabaseManager(DB_PATH)
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

    async def connect(self):
        """Connects and authenticates the Telegram client."""
        logger.info("Connecting to Telegram client...")
        await self.client.start(phone=PHONE if PHONE else None)
        me = await self.client.get_me()
        logger.info(f"Successfully logged in as: {me.first_name} (@{me.username or 'No Username'}) [ID: {me.id}]")

    async def get_database_folder_channels(self, target_folder_name: str = FOLDER_NAME) -> List[types.TypeInputPeer]:
        """
        Finds the Telegram chat folder named `target_folder_name` (case-insensitive)
        and returns all channel/chat entities in it.
        """
        logger.info(f"Looking for chat folder: '{target_folder_name}'...")
        try:
            filters_response = await self.client(functions.messages.GetDialogFiltersRequest())
        except Exception as e:
            logger.error(f"Failed to fetch dialog filters: {e}")
            return []

        target_filter = None
        available_folders = []

        for f in filters_response.filters:
            # DialogFilter has title
            title = None
            if hasattr(f, 'title'):
                if hasattr(f.title, 'text'):
                    title = f.title.text
                elif isinstance(f.title, str):
                    title = f.title
                else:
                    title = str(f.title)

            if title:
                available_folders.append(title)
                if title.strip().lower() == target_folder_name.strip().lower():
                    target_filter = f
                    break

        if not target_filter:
            logger.warning(f"Could not find folder '{target_folder_name}'. Available folders found: {available_folders}")
            return []

        logger.info(f"Found folder '{target_folder_name}'! Extracting channels...")

        channels = []
        # include_peers contains input peers
        for peer in target_filter.include_peers:
            try:
                entity = await self.client.get_entity(peer)
                channels.append(entity)
            except Exception as e:
                logger.warning(f"Could not resolve peer {peer}: {e}")

        logger.info(f"Discovered {len(channels)} channels/chats inside folder '{target_folder_name}'.")
        for idx, ch in enumerate(channels, 1):
            title = getattr(ch, 'title', getattr(ch, 'username', 'Unknown'))
            logger.info(f"  {idx}. {title} (ID: {ch.id})")

        return channels

    async def scrape_channel(self, channel_entity):
        """Scrapes all waifu artwork and metadata from a specific channel."""
        channel_id = channel_entity.id
        channel_title = getattr(channel_entity, 'title', f"channel_{channel_id}")
        channel_username = getattr(channel_entity, 'username', None)

        safe_channel_folder = sanitize_filename(channel_title)
        channel_image_dir = IMAGE_DIR / safe_channel_folder
        channel_image_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"\n=======================================================")
        logger.info(f"Starting scrape for: {channel_title} (ID: {channel_id})")
        logger.info(f"=======================================================")

        count = 0
        skipped = 0

        # Retrieve messages in reverse (from oldest to newest) for proper indexing
        async for message in self.client.iter_messages(channel_entity, reverse=True):
            if not message or not isinstance(message, types.Message):
                continue

            msg_id = message.id
            raw_text = getattr(message, 'raw_text', '') or getattr(message, 'text', '') or getattr(message, 'message', '') or ""

            # Check if message has media (Photo or Document image)
            has_photo = getattr(message, 'photo', None) is not None
            doc = getattr(message, 'document', None)
            has_image_doc = (
                doc is not None and
                getattr(doc, 'mime_type', None) is not None and
                doc.mime_type.startswith("image/")
            )

            # Skip messages without media unless they contain character info
            if not (has_photo or has_image_doc) and not raw_text.strip():
                continue

            # Check if already processed
            if self.db.is_message_processed(channel_id, msg_id):
                skipped += 1
                continue

            # Parse character metadata
            parsed = WaifuParser.parse(raw_text)
            char_name = parsed["name"]
            anime = parsed["anime"]
            rarity = parsed["rarity"]
            char_id = parsed["character_id"]
            event = parsed["event"]
            extra_info = parsed["extra_info"]

            image_path = None
            image_filename = None

            # Download photo if requested
            if DOWNLOAD_IMAGES and (has_photo or has_image_doc):
                safe_char_name = sanitize_filename(char_name)
                image_filename = f"{safe_char_name}_{msg_id}.jpg"
                dest_file_path = channel_image_dir / image_filename

                if not dest_file_path.exists():
                    retries = 3
                    while retries > 0:
                        try:
                            await self.client.download_media(message, file=str(dest_file_path))
                            image_path = str(dest_file_path.resolve())
                            break
                        except errors.FloodWaitError as fwe:
                            logger.warning(f"FloodWait encountered! Sleeping for {fwe.seconds + 2}s...")
                            await asyncio.sleep(fwe.seconds + 2)
                        except Exception as e:
                            logger.warning(f"Error downloading media for msg {msg_id}: {e}")
                            retries -= 1
                            await asyncio.sleep(1)
                else:
                    image_path = str(dest_file_path.resolve())

            # Save into Database
            self.db.save_character(
                telegram_msg_id=msg_id,
                channel_id=channel_id,
                channel_title=channel_title,
                name=char_name,
                anime=anime,
                rarity=rarity,
                character_id=char_id,
                event=event,
                image_path=image_path,
                image_filename=image_filename,
                extra_info=extra_info,
                raw_text=raw_text
            )

            self.db.update_channel_progress(channel_id, channel_title, channel_username, msg_id)
            count += 1

            if count % 25 == 0:
                logger.info(f"[{channel_title}] Scraped {count} waifus so far (Skipped {skipped} already in DB)...")

            # Mild throttle to prevent aggressive spamming
            await asyncio.sleep(0.05)

        logger.info(f"Completed channel '{channel_title}': Scraped {count} new waifus, {skipped} skipped.")

    async def run(self):
        """Main execution flow."""
        await self.connect()

        channels = await self.get_database_folder_channels(FOLDER_NAME)
        if not channels:
            logger.error(
                f"No channels found in folder '{FOLDER_NAME}'. "
                f"Please ensure the folder name matches your Telegram chat folder."
            )
            return

        logger.info(f"\nProcessing {len(channels)} channels...")
        for channel in channels:
            try:
                await self.scrape_channel(channel)
            except errors.FloodWaitError as fwe:
                logger.warning(f"Global FloodWait: Sleeping for {fwe.seconds + 5}s...")
                await asyncio.sleep(fwe.seconds + 5)
            except Exception as e:
                logger.error(f"Error processing channel {getattr(channel, 'title', channel.id)}: {e}", exc_info=True)

        # Final Exports and Summary
        logger.info("\n" + "=" * 60)
        logger.info("SCRAPING COMPLETED! EXPORTING DATABASE...")
        logger.info("=" * 60)

        json_count = self.db.export_to_json(EXPORT_JSON_PATH)
        csv_count = self.db.export_to_csv(EXPORT_CSV_PATH)
        stats = self.db.get_stats()

        logger.info(f"Exported {json_count} characters to: {EXPORT_JSON_PATH}")
        logger.info(f"Exported {csv_count} characters to: {EXPORT_CSV_PATH}")
        logger.info(f"Total Characters in DB: {stats['total_characters']}")
        logger.info(f"Unique Anime Franchises: {stats['unique_animes']}")
        logger.info(f"SQLite Database File: {DB_PATH}")


async def main():
    errors_list = validate_config()
    if errors_list:
        print("\n[CONFIGURATION ERROR]")
        for err in errors_list:
            print(f" - {err}")
        print("\nPlease copy .env.example to .env and fill in your TG_API_ID and TG_API_HASH from https://my.telegram.org\n")
        sys.exit(1)

    scraper = TelegramWaifuScraper()
    await scraper.run()


if __name__ == "__main__":
    asyncio.run(main())
