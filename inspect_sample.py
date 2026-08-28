import asyncio
import sys
from telethon import TelegramClient, functions, types

from config import API_ID, API_HASH, PHONE, SESSION_NAME, FOLDER_NAME, validate_config
from parser import WaifuParser


async def inspect_channels():
    errors_list = validate_config()
    if errors_list:
        print("\n[CONFIGURATION ERROR]")
        for err in errors_list:
            print(f" - {err}")
        print("\nPlease setup your .env file first.\n")
        sys.exit(1)

    print("Connecting to Telegram...")
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start(phone=PHONE if PHONE else None)

    me = await client.get_me()
    print(f"Logged in as: {me.first_name} (@{me.username or 'No Username'}) [ID: {me.id}]")

    print(f"\nScanning for folder: '{FOLDER_NAME}'...")
    try:
        filters_response = await client(functions.messages.GetDialogFiltersRequest())
    except Exception as e:
        print(f"Error fetching folders: {e}")
        await client.disconnect()
        return

    target_filter = None
    all_folder_names = []

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
            all_folder_names.append(title)
            if title.strip().lower() == FOLDER_NAME.strip().lower():
                target_filter = f
                break

    if not target_filter:
        print(f"\n[WARNING] Folder '{FOLDER_NAME}' was not found!")
        print(f"Folders available in your Telegram account: {all_folder_names}")
        await client.disconnect()
        return

    print(f"[SUCCESS] Found folder '{FOLDER_NAME}'!")
    channels = []
    for peer in target_filter.include_peers:
        try:
            entity = await client.get_entity(peer)
            channels.append(entity)
        except Exception as e:
            print(f"Could not resolve peer {peer}: {e}")

    print(f"Found {len(channels)} channels inside '{FOLDER_NAME}':\n")
    for idx, ch in enumerate(channels, 1):
        title = getattr(ch, 'title', 'Unknown')
        print(f"  {idx}. {title} (ID: {ch.id})")

    print("\n" + "=" * 60)
    print("INSPECTING SAMPLE POSTS & PARSER OUTPUT (First 2 posts per channel)")
    print("=" * 60)

    for ch in channels:
        title = getattr(ch, 'title', f"Channel {ch.id}")
        print(f"\n--- [Channel: {title}] ---")
        sample_count = 0

        async for message in client.iter_messages(ch, limit=20):
            if not message or not isinstance(message, types.Message):
                continue

            raw_text = getattr(message, 'raw_text', '') or getattr(message, 'text', '') or getattr(message, 'message', '') or ""
            has_photo = getattr(message, 'photo', None) is not None
            doc = getattr(message, 'document', None)
            has_media = has_photo or (
                doc is not None and
                getattr(doc, 'mime_type', None) is not None and
                doc.mime_type.startswith("image/")
            )

            if not raw_text.strip() and not has_media:
                continue

            sample_count += 1
            parsed = WaifuParser.parse(raw_text)

            print(f"\nMessage ID #{message.id} (Has Photo: {has_media}):")
            print(f"  [RAW TEXT]:\n    {repr(raw_text)}")
            print(f"  [PARSED DATA]:")
            print(f"    - Character Name : {parsed['name']}")
            print(f"    - Anime / Source : {parsed['anime']}")
            print(f"    - Rarity / Tier  : {parsed['rarity']}")
            print(f"    - Character ID   : {parsed['character_id']}")
            print(f"    - Event / Tag    : {parsed['event']}")
            if parsed['extra_info']:
                print(f"    - Extra Metadata : {parsed['extra_info']}")

            if sample_count >= 2:
                break

        if sample_count == 0:
            print("  (No messages with captions or photos found in the recent 20 messages)")

    print("\n" + "=" * 60)
    print("Inspection complete! If parsed data looks accurate, run: py scraper.py")
    print("=" * 60 + "\n")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(inspect_channels())
