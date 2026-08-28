from parser import WaifuParser
from database import DatabaseManager
import tempfile
from pathlib import Path


def test_parser():
    # Test case 1: Standard key-value format
    sample1 = """
    🌸 Name: Rem
    🎬 Anime: Re:Zero kara Hajimeru Isekai Seikatsu
    👑 Rarity: ⭐⭐⭐⭐⭐ Legendary
    🆔 ID: #REM-001
    """
    res1 = WaifuParser.parse(sample1)
    assert res1["name"] == "Rem", f"Expected Rem, got {res1['name']}"
    assert "Re:Zero" in res1["anime"], f"Expected Re:Zero, got {res1['anime']}"
    assert "Legendary" in res1["rarity"] or "5 Stars" in res1["rarity"]
    assert res1["character_id"] == "REM-001"

    # Test case 2: Separator format
    sample2 = "Mikasa Ackerman - Attack on Titan"
    res2 = WaifuParser.parse(sample2)
    assert res2["name"] == "Mikasa Ackerman"
    assert res2["anime"] == "Attack on Titan"

    # Test case 3: Bracket format
    sample3 = "【Marin Kitagawa】\nMy Dress-Up Darling\n⭐⭐⭐⭐"
    res3 = WaifuParser.parse(sample3)
    assert res3["name"] == "Marin Kitagawa"
    assert res3["anime"] == "My Dress-Up Darling"
    assert "4 Stars" in res3["rarity"]

    print("[PASSED] Parser unit tests passed!")


def test_database():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        json_path = Path(tmpdir) / "test.json"
        csv_path = Path(tmpdir) / "test.csv"

        db = DatabaseManager(db_path)
        db.save_character(
            telegram_msg_id=101,
            channel_id=-100123456,
            channel_title="Waifu Channel 1",
            name="Asuka Langley",
            anime="Neon Genesis Evangelion",
            rarity="UR",
            character_id="EVA-02",
            event="Summer 2024",
            image_path="/path/to/asuka.jpg",
            image_filename="asuka.jpg",
            extra_info={"rank": "1"},
            raw_text="Asuka Langley from Evangelion"
        )

        assert db.is_message_processed(-100123456, 101) is True
        assert db.is_message_processed(-100123456, 999) is False

        stats = db.get_stats()
        assert stats["total_characters"] == 1
        assert stats["unique_animes"] == 1

        json_count = db.export_to_json(json_path)
        assert json_count == 1
        assert json_path.exists()

        csv_count = db.export_to_csv(csv_path)
        assert csv_count == 1
        assert csv_path.exists()

        print("[PASSED] Database & export unit tests passed!")


if __name__ == "__main__":
    test_parser()
    test_database()
