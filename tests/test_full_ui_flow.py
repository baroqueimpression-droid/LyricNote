import json
from pathlib import Path
from src.config import BASE_DATA_DIR, BACKUP_DIR, DB_PATH
from src.db import init_db, get_album_list, get_album_tracks, get_connection
import src.app as app

def run_exhaustive_ui_tests():
    print("==================================================")
    print("  Lylic 全UIイベント・エンドツーエンド総合検証")
    print("==================================================")
    init_db()

    # 1. テストデータの準備 (DBに安全に投入)
    conn = get_connection()
    conn.execute("DELETE FROM tracks WHERE artist = 'TestArtist'")
    conn.execute("DELETE FROM albums WHERE artist = 'TestArtist'")
    conn.commit()
    conn.close()

    from src.db import upsert_track
    test_track_1 = {
        "persistent_id": "TEST_PID_0001",
        "name": "Roundabout",
        "artist": "TestArtist",
        "album": "Fragile",
        "year": 1971,
        "track_number": 1,
        "disc_number": 1,
        "duration": 515,
        "has_original_lyrics": 0,
        "original_lyrics": "I'll be the roundabout\nThe words will make you out 'n' out",
        "language": "en",
        "status": "unprocessed"
    }
    test_track_2 = {
        "persistent_id": "TEST_PID_0002",
        "name": "Cans and Brahms",
        "artist": "TestArtist",
        "album": "Fragile",
        "year": 1971,
        "track_number": 2,
        "disc_number": 1,
        "duration": 98,
        "has_original_lyrics": 0,
        "original_lyrics": "",
        "language": "en",
        "status": "unprocessed"
    }
    upsert_track(test_track_1)
    upsert_track(test_track_2)
    print("[1/8] Test data inserted: OK")

    # 2. refresh_album_dropdown のテスト
    dropdown_update = app.refresh_album_dropdown()
    choices = dropdown_update.get("choices", [])
    assert any("TestArtist - Fragile" in c for c in choices), "ドロップダウンにテストアルバムが含まれていません"
    print(f"[2/8] Album dropdown refresh: OK (Count: {len(choices)})")

    # 3. on_select_album のテスト
    choice_str = next(c for c in choices if "TestArtist - Fragile" in c)
    info, liner, context_json, track_drop, first_orig, first_trans, first_comm, first_status, write_res = app.on_select_album(choice_str)
    assert "TestArtist - Fragile" in info
    assert "2 曲" in info
    assert len(track_drop.get("choices", [])) == 2
    assert "roundabout" in first_orig.lower()
    print("[3/8] Album select event (on_select_album): OK")

    # 4. on_generate_critic_prompt のテスト
    prompt = app.on_generate_critic_prompt(choice_str)
    assert "TestArtist" in prompt
    assert "Fragile" in prompt
    assert "Roundabout" in prompt
    print("[4/8] Gemini prompt generation (on_generate_critic_prompt): OK")

    # 5. on_apply_critic_json のテスト
    sample_json = """
```json
{
  "is_live": false,
  "liner_notes": "イエスの代表作『こわれもの』。各メンバーのソロ楽曲とバンドアンサンブルの結晶。",
  "story_and_characters": "鮮烈なアコースティックギターと重厚なベースリフが織りなす音像風景。",
  "glossary": [
    {
      "term": "Roundabout",
      "meaning": "環状交差点。転じて人生の紆余曲折や循環。",
      "translation_hint": "単なる交差点ではなく、めぐる運命のイメージで訳すこと"
    }
  ]
}
```
"""
    status, liner_res, context_res = app.on_apply_critic_json(choice_str, sample_json)
    assert "保存しました" in status
    assert "イエスの代表作" in liner_res
    assert "Roundabout" in context_res
    print("[5/8] Gemini JSON apply (on_apply_critic_json): OK")

    # 6. on_select_track のテスト
    t_orig, t_trans, t_comm, t_status = app.on_select_track(choice_str, "01. Roundabout")
    assert "I'll be the roundabout" in t_orig
    print("[6/8] Track select event (on_select_track): OK")

    # 7. タブ3「洋楽ライナーノーツ辞典」表示テスト
    dict_md = app.on_select_dict_album(choice_str)
    assert "TestArtist - Fragile" in dict_md
    assert "イエスの代表作" in dict_md
    print("[7/8] Liner notes dictionary display (on_select_dict_album): OK")

    # 8. 安全ルールチェック（DB・バックアップパスの検証）
    assert BASE_DATA_DIR.exists()
    assert DB_PATH.exists()
    assert BACKUP_DIR.exists()
    print("[8/8] Data directory security check: OK")

    print("\nALL UI FLOW TESTS COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    run_exhaustive_ui_tests()
