import io
import sys
import asyncio
import logging
from typing import List, Tuple

from telethon import TelegramClient, types, errors
from tqdm import tqdm

from config import (
    API_ID,
    API_HASH,
    PHONE,
    SESSION_NAME,
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


class WaifuVisualHasher:
    """In-memory visual hashing utility for characters missing visual fingerprints."""

    def __init__(self):
        self.db = DatabaseManager(DB_PATH)
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        self.semaphore = asyncio.Semaphore(CONCURRENT_DOWNLOADS * 2)
        self.db_lock = asyncio.Lock()

    async def connect(self):
        await self.client.start(phone=PHONE if PHONE else None)
        me = await self.client.get_me()
        logger.info(f"Logged in as: {me.first_name} [ID: {me.id}]")
        logger.info(f"⚡ In-Memory Hashing: Parallel workers = {CONCURRENT_DOWNLOADS * 2}")

    async def _hash_message(self, channel_id: int, msg_id: int, char_id: int) -> Optional[Tuple[str, str, int]]:
        async with self.semaphore:
            try:
                msg = await self.client.get_messages(channel_id, ids=msg_id)
                if not msg or not (getattr(msg, 'photo', None) or getattr(msg, 'document', None)):
                    return None

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

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, channel_id, telegram_msg_id, name
                FROM characters
                WHERE (image_phash IS NULL OR image_phash = '') AND name != 'Unknown'
                ORDER BY id ASC
            """)
            unhashed_rows = cursor.fetchall()

        total = len(unhashed_rows)
        if total == 0:
            logger.info("✅ All characters in database already have visual hashes!")
            return

        logger.info(f"⚡ Computing in-memory visual hashes for {total:,} characters...")

        batch_size = CONCURRENT_DOWNLOADS * 4
        updates = []
        pbar = tqdm(total=total, desc="Hashing in RAM")

        for i in range(0, total, batch_size):
            batch = unhashed_rows[i:i + batch_size]
            tasks = [self._hash_message(r["channel_id"], r["telegram_msg_id"], r["id"]) for r in batch]
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

            pbar.update(len(batch))

        pbar.close()
        self.db.export_to_json(EXPORT_JSON_PATH)
        stats = self.db.get_stats()
        logger.info(f"✅ Hashing Complete! Total visual models active: {stats['hashed_characters']:,}")


async def main():
    errors_list = validate_config()
    if errors_list:
        print("[CONFIG ERROR]", errors_list)
        sys.exit(1)

    hasher = WaifuVisualHasher()
    await hasher.run()


if __name__ == "__main__":
    asyncio.run(main())
