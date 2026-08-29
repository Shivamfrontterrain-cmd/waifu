import io
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
    CONCURRENT_DOWNLOADS,
    DB_PATH,
    EXPORT_JSON_PATH,
    EXPORT_CSV_PATH,
    validate_config
)
from database import DatabaseManager
from parser import WaifuParser
from matcher import compute_image_hashes

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger("WaifuScraper")


class TelegramWaifuScraper:
    """In-memory visual hashing scraper (computes visual hashes in RAM, 0 MB disk storage used)."""

    def __init__(self):
        self.db = DatabaseManager(DB_PATH)
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        self.semaphore = asyncio.Semaphore(CONCURRENT_DOWNLOADS)
        self.db_lock = asyncio.Lock()

    async def connect(self):
        """Connects and authenticates the Telegram client."""
        logger.info("Connecting to Telegram client...")
        await self.client.start(phone=PHONE if PHONE else None)
        me = await self.client.get_me()
        logger.info(f"Successfully logged in as: {me.first_name} (@{me.username or 'No Username'}) [ID: {me.id}]")
        logger.info(f"⚡ In-Memory Visual Streaming Active: Computing 64-bit visual hashes in RAM (0 MB disk space used)")
        logger.info(f"Parallel streams: {CONCURRENT_DOWNLOADS} active workers")

    async def get_database_folder_channels(self, target_folder_name: str = FOLDER_NAME) -> List[types.TypeInputPeer]:
        """Finds the Telegram chat folder and extracts all channels inside it."""
        logger.info(f"Looking for chat folder: '{target_folder_name}'...")
        try:
            filters_response = await self.client(functions.messages.GetDialogFiltersRequest())
        except Exception as e:
            logger.error(f"Failed to fetch dialog filters: {e}")
            return []

        target_filter = None
        available_folders = []

        for f in filters_response.filters:
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

    async def _process_message(self, message: types.Message, channel_id: int, channel_title: str) -> bool:
        """Extracts character metadata and computes in-memory visual hashes in RAM."""
        msg_id = message.id
        raw_text = getattr(message, 'raw_text', '') or getattr(message, 'text', '') or getattr(message, 'message', '') or ""

        has_photo = getattr(message, 'photo', None) is not None
        doc = getattr(message, 'document', None)
        has_image_doc = (
            doc is not None and
            getattr(doc, 'mime_type', None) is not None and
            doc.mime_type.startswith("image/")
        )

        if not (has_photo or has_image_doc) and not raw_text.strip():
            return False

        # Parse character metadata
        parsed = WaifuParser.parse(raw_text)
        char_name = parsed["name"]
        anime = parsed["anime"]
        rarity = parsed["rarity"]
        char_id = parsed["character_id"]
        event = parsed["event"]
        extra_info = parsed["extra_info"]

        image_phash = None
        image_dhash = None
        telegram_file_unique_id = None

        if has_photo:
            telegram_file_unique_id = str(getattr(message.photo, 'id', ''))
        elif has_image_doc:
            telegram_file_unique_id = str(getattr(doc, 'id', ''))

        # In-Memory Visual Hashing (Streams thumbnail directly in RAM, 0 MB disk storage used)
        if has_photo or has_image_doc:
            async with self.semaphore:
                retries = 2
                while retries > 0:
                    try:
                        mem_buffer = io.BytesIO()
                        # Download thumbnail into RAM
                        await self.client.download_media(message, file=mem_buffer, thumb=-1)
                        mem_buffer.seek(0)
                        hashes = compute_image_hashes(mem_buffer)
                        if hashes:
                            image_phash, image_dhash, _, _ = hashes
                        break
                    except errors.FloodWaitError as fwe:
                        logger.warning(f"FloodWait on msg {msg_id}: Waiting {fwe.seconds + 1}s...")
                        await asyncio.sleep(fwe.seconds + 1)
                    except Exception:
                        retries -= 1
                        await asyncio.sleep(0.2)

        # Thread-safe database insert
        async with self.db_lock:
            self.db.save_character(
                telegram_msg_id=msg_id,
                channel_id=channel_id,
                channel_title=channel_title,
                name=char_name,
                anime=anime,
                rarity=rarity,
                character_id=char_id,
                event=event,
                telegram_file_id=None,
                telegram_file_unique_id=telegram_file_unique_id,
                image_phash=image_phash,
                image_dhash=image_dhash,
                extra_info=extra_info,
                raw_text=raw_text
            )
        return True

    async def scrape_channel(self, channel_entity):
        """Scrapes channel messages and visual hashes in parallel batches."""
        channel_id = channel_entity.id
        channel_title = getattr(channel_entity, 'title', f"channel_{channel_id}")
        channel_username = getattr(channel_entity, 'username', None)

        logger.info(f"\n=======================================================")
        logger.info(f"Scraping channel: {channel_title} (ID: {channel_id})")
        logger.info(f"=======================================================")

        count = 0
        skipped = 0
        batch_tasks = []
        BATCH_SIZE = CONCURRENT_DOWNLOADS * 3

        async for message in self.client.iter_messages(channel_entity, reverse=True):
            if not message or not isinstance(message, types.Message):
                continue

            msg_id = message.id

            # Check if already processed with a visual hash
            if self.db.is_message_processed(channel_id, msg_id):
                skipped += 1
                continue

            task = self._process_message(message, channel_id, channel_title)
            batch_tasks.append(task)

            if len(batch_tasks) >= BATCH_SIZE:
                results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                new_saved = sum(1 for r in results if r is True)
                count += new_saved
                batch_tasks = []
                self.db.update_channel_progress(channel_id, channel_title, channel_username, msg_id)
                logger.info(f"[{channel_title}] Scraped & Visually Hashed {count} waifus | Skipped {skipped}...")

        if batch_tasks:
            results = await asyncio.gather(*batch_tasks, return_exceptions=True)
            new_saved = sum(1 for r in results if r is True)
            count += new_saved
            batch_tasks = []
            self.db.update_channel_progress(channel_id, channel_title, channel_username, msg_id)

        logger.info(f"✅ Finished '{channel_title}': {count} new waifus hashed, {skipped} skipped.")

    async def run(self):
        """Main execution loop."""
        await self.connect()

        channels = await self.get_database_folder_channels(FOLDER_NAME)
        if not channels:
            logger.error(f"No channels found in folder '{FOLDER_NAME}'.")
            return

        logger.info(f"\nProcessing {len(channels)} channels...")
        for channel in channels:
            try:
                await self.scrape_channel(channel)
            except errors.FloodWaitError as fwe:
                logger.warning(f"FloodWait: Sleeping for {fwe.seconds + 2}s...")
                await asyncio.sleep(fwe.seconds + 2)
            except Exception as e:
                logger.error(f"Error processing channel {getattr(channel, 'title', channel.id)}: {e}", exc_info=True)

        logger.info("\n" + "=" * 60)
        logger.info("SCRAPING COMPLETE! EXPORTING DATABASE WITH VISUAL HASHES...")
        logger.info("=" * 60)

        json_count = self.db.export_to_json(EXPORT_JSON_PATH)
        csv_count = self.db.export_to_csv(EXPORT_CSV_PATH)
        stats = self.db.get_stats()

        logger.info(f"Exported {json_count} characters to: {EXPORT_JSON_PATH}")
        logger.info(f"Exported {csv_count} characters to: {EXPORT_CSV_PATH}")
        logger.info(f"Total Characters in DB: {stats['total_characters']}")
        logger.info(f"Total Visually Hashed Characters: {stats['hashed_characters']}")
        logger.info(f"Unique Anime Franchises: {stats['unique_animes']}")


async def main():
    errors_list = validate_config()
    if errors_list:
        print("\n[CONFIGURATION ERROR]")
        for err in errors_list:
            print(f" - {err}")
        print("\nPlease check your .env file.\n")
        sys.exit(1)

    scraper = TelegramWaifuScraper()
    await scraper.run()


if __name__ == "__main__":
    asyncio.run(main())
