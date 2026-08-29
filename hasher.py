import io
import sys
import asyncio
import logging
from typing import List, Dict, Tuple, Optional, Any

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
    validate_config
)
from database import DatabaseManager
from matcher import compute_image_hashes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger("WaifuHasher")

NUM_WORKERS = max(CONCURRENT_DOWNLOADS * 3, 24)
CHUNK_SIZE = 100  # 100 messages fetched in a single Telegram RPC call


class TargetedVisualHasher:
    """Targeted ultra-fast visual hasher using embedded stripped thumbnails and parallel RAM workers."""

    def __init__(self, num_workers: int = NUM_WORKERS):
        self.db = DatabaseManager(DB_PATH)
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        self.num_workers = num_workers
        self.semaphore = asyncio.Semaphore(num_workers)

    async def connect(self):
        await self.client.start(phone=PHONE if PHONE else None)
        me = await self.client.get_me()
        logger.info(f"Logged in as: {me.first_name} [ID: {me.id}]")
        logger.info(f"⚡ Ultra-Fast Visual Hasher: {self.num_workers} parallel RAM workers, RPC Chunk = {CHUNK_SIZE}")

    async def get_database_folder_channels(self, target_folder_name: str = FOLDER_NAME) -> List[Any]:
        """Finds the Telegram chat folder and extracts all channel entities inside it."""
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
            logger.warning(f"Could not find folder '{target_folder_name}'. Available: {available_folders}")
            return []

        channels = []
        for peer in target_filter.include_peers:
            try:
                entity = await self.client.get_entity(peer)
                channels.append(entity)
            except Exception:
                pass

        logger.info(f"Discovered {len(channels)} channels in folder '{target_folder_name}'.")
        return channels

    async def _download_and_hash(self, msg: types.Message, char_id: int) -> Optional[Tuple[str, str, int]]:
        if not msg:
            return None

        has_photo = getattr(msg, 'photo', None) is not None
        doc = getattr(msg, 'document', None)
        has_image_doc = (
            doc is not None and
            getattr(doc, 'mime_type', None) is not None and
            doc.mime_type.startswith("image/")
        )

        if not (has_photo or has_image_doc):
            return None

        async with self.semaphore:
            try:
                buf = io.BytesIO()
                # thumb=0 extracts the embedded stripped mini-thumbnail directly from the message object in 0ms!
                await self.client.download_media(msg, file=buf, thumb=0)
                buf.seek(0)
                hashes = compute_image_hashes(buf)
                if hashes:
                    p_str, d_str, _, _ = hashes
                    return p_str, d_str, char_id
            except Exception:
                pass

            # Fallback to standard thumbnail if stripped thumbnail is absent
            try:
                buf = io.BytesIO()
                await self.client.download_media(msg, file=buf, thumb=-1)
                buf.seek(0)
                hashes = compute_image_hashes(buf)
                if hashes:
                    p_str, d_str, _, _ = hashes
                    return p_str, d_str, char_id
            except errors.FloodWaitError as fwe:
                logger.warning(f"FloodWait: Sleeping {fwe.seconds + 1}s...")
                await asyncio.sleep(fwe.seconds + 1)
            except Exception:
                pass
        return None

    async def run(self):
        await self.connect()

        # Count total unhashed
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM characters
                WHERE (image_phash IS NULL OR image_phash = '') AND name != 'Unknown'
            """)
            total_unhashed = cursor.fetchone()[0]

        if total_unhashed == 0:
            logger.info("✅ All characters in database already have visual hashes!")
            return

        logger.info(f"⚡ Targeted in-memory visual hashing for {total_unhashed:,} unhashed characters...")

        channels = await self.get_database_folder_channels(FOLDER_NAME)
        if not channels:
            logger.error("No channels available in folder.")
            return

        channel_map = {ch.id: ch for ch in channels}

        pbar = tqdm(total=total_unhashed, desc="⚡ Visual Hashing in RAM", unit="img")
        total_saved = 0

        for ch_id, ch_entity in channel_map.items():
            ch_title = getattr(ch_entity, 'title', f"channel_{ch_id}")

            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT telegram_msg_id, id FROM characters
                    WHERE channel_id = ? AND (image_phash IS NULL OR image_phash = '') AND name != 'Unknown'
                    ORDER BY telegram_msg_id ASC
                """, (ch_id,))
                rows = cursor.fetchall()

            if not rows:
                continue

            msg_to_char = {r["telegram_msg_id"]: r["id"] for r in rows}
            msg_ids = list(msg_to_char.keys())

            logger.info(f"\nProcessing '{ch_title}' ({len(msg_ids):,} unhashed items)...")

            for i in range(0, len(msg_ids), CHUNK_SIZE):
                chunk = msg_ids[i:i + CHUNK_SIZE]

                try:
                    messages = await self.client.get_messages(ch_entity, ids=chunk)
                except errors.FloodWaitError as fwe:
                    logger.warning(f"FloodWait on get_messages: Sleeping {fwe.seconds + 1}s...")
                    await asyncio.sleep(fwe.seconds + 1)
                    try:
                        messages = await self.client.get_messages(ch_entity, ids=chunk)
                    except Exception:
                        messages = []
                except Exception as e:
                    logger.debug(f"Error fetching batch: {e}")
                    messages = []

                if not messages:
                    pbar.update(len(chunk))
                    continue

                tasks = []
                for msg in messages:
                    if msg and msg.id in msg_to_char:
                        tasks.append(self._download_and_hash(msg, msg_to_char[msg.id]))

                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    batch_updates = [r for r in results if isinstance(r, tuple)]

                    if batch_updates:
                        with self.db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.executemany(
                                "UPDATE characters SET image_phash = ?, image_dhash = ? WHERE id = ?",
                                batch_updates
                            )
                            conn.commit()
                        total_saved += len(batch_updates)

                pbar.update(len(chunk))

        pbar.close()

        logger.info("💾 Exporting updated database to JSON...")
        self.db.export_to_json(EXPORT_JSON_PATH)

        stats = self.db.get_stats()
        logger.info(f"✅ Complete! Total hashed visual characters: {stats['hashed_characters']:,} (+{total_saved:,} newly hashed)")


async def main():
    errors_list = validate_config()
    if errors_list:
        print("[CONFIG ERROR]", errors_list)
        sys.exit(1)

    hasher = TargetedVisualHasher(num_workers=NUM_WORKERS)
    await hasher.run()


if __name__ == "__main__":
    asyncio.run(main())
