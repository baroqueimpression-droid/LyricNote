import sqlite3
from pathlib import Path
from src.config import assert_safe_path, BASE_DATA_DIR, DB_PATH
from src.db import (
    init_db, upsert_track, get_connection, update_track_lyrics, 
    update_track_translation, get_album_list, get_album_tracks,
    save_translation_memory, get_cached_translation
)

def test_safety_rule():
    # 正常系: X:\LylicData 配下
    safe_path = BASE_DATA_DIR / "test.txt"
    assert assert_safe_path(safe_path) == safe_path.resolve()

    # 異常系: C:\ や X:\OtherFolder
    try:
        assert_safe_path("C:/Windows/System32")
        assert False, "PermissionError should be raised"
    except PermissionError:
        pass

    try:
        assert_safe_path("X:/OtherFolder/music.mp3")
        assert False, "PermissionError should be raised"
    except PermissionError:
        pass

def test_db_operations():
    init_db()
    
    # トラック挿入
    track = {
        "persistent_id": "TESTPID12345678",
        "name": "Close to the Edge",
        "artist": "Yes",
        "album": "Close to the Edge",
        "year": 1972,
        "track_number": 1,
        "disc_number": 1,
        "duration": 1121,
        "has_original_lyrics": 0,
        "original_lyrics": "",
        "language": "en",
        "status": "unprocessed"
    }
    upsert_track(track)
    
    # 取得確認
    albums = get_album_list()
    assert any(a["artist"] == "Yes" and a["album"] == "Close to the Edge" for a in albums)

    tracks = get_album_tracks("Yes", "Close to the Edge")
    assert len(tracks) == 1
    assert tracks[0]["name"] == "Close to the Edge"

    # 歌詞更新
    sample_lyrics = "A seasoned witch could call you from the depths of your disgrace"
    update_track_lyrics("TESTPID12345678", sample_lyrics)
    
    tracks = get_album_tracks("Yes", "Close to the Edge")
    assert tracks[0]["status"] == "lyrics_found"
    assert tracks[0]["original_lyrics"] == sample_lyrics

    # 翻訳メモリテスト
    sample_translation = "熟練した魔女が、お前の不名誉の底から呼び戻すだろう"
    save_translation_memory("Yes", "Close to the Edge", sample_lyrics, sample_translation)

    cached = get_cached_translation(sample_lyrics)
    assert cached == sample_translation

    print("Phase 1 Tests Passed Successfully!")

if __name__ == "__main__":
    test_safety_rule()
    test_db_operations()
