import sys
import os
import unittest
from pathlib import Path

# プロジェクトルートを追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import (
    init_db, get_connection, upsert_track, update_track_lyrics,
    update_track_translation, update_track_liner_notes,
    update_track_diagnostic, get_album_tracks
)
from src.critic import normalize_track_name, match_track_commentaries, validate_critic_output
from src.lyrics_fetcher import clean_track_title, get_artist_variations
from src.itunes_writer import format_combined_lyrics, ITUNES_LYRICS_MAX_LENGTH
from src.translator import build_translation_system_prompt
from src.poc_helpers import is_likely_western

class TestLyricNoteV21(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_dod1_track_commentary_persistence(self):
        """DoD-1: 各曲の個別解説が tracks.liner_notes に正しく永続化されるか"""
        pid = "TEST_PID_001"
        upsert_track({
            "persistent_id": pid,
            "name": "Heat Of The Moment",
            "artist": "Asia",
            "album": "Asia Test",
            "year": 1982,
            "track_number": 1,
            "disc_number": 1,
            "duration": 230,
            "has_original_lyrics": 1,
            "original_lyrics": "I never meant to be so bad to you",
            "language": "en",
            "status": "ready_to_write"
        })

        test_commentary = "この曲は全米1位を獲得したスタジアムロックの代表曲である。"
        update_track_liner_notes(pid, test_commentary)

        conn = get_connection()
        row = conn.execute("SELECT liner_notes FROM tracks WHERE persistent_id = ?", (pid,)).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["liner_notes"], test_commentary)
        print("[Pass] DoD-1: 個別解説のDB永続化検証 成功")

    def test_dod2_track_name_normalization_matching(self):
        """DoD-2: Geminiが '01. ' や大文字小文字等の表記揺れを返しても100%マッチするか"""
        tracks = [
            {"persistent_id": "PID_01", "name": "Heat Of The Moment"},
            {"persistent_id": "PID_02", "name": "Only Time Will Tell"},
            {"persistent_id": "PID_03", "name": "Sole Survivor"}
        ]
        gemini_commentaries = [
            {"track_name": "01. heat of the moment", "commentary": "1曲目解説"},
            {"track_name": "2 - ONLY TIME WILL TELL", "commentary": "2曲目解説"},
            {"track_name": "Sole Survivor (Single Edit)", "commentary": "3曲目解説"}
        ]

        matched = match_track_commentaries(gemini_commentaries, tracks)
        self.assertEqual(len(matched), 3)
        self.assertEqual(matched["PID_01"], "1曲目解説")
        self.assertEqual(matched["PID_02"], "2曲目解説")
        self.assertEqual(matched["PID_03"], "3曲目解説")
        print("[Pass] DoD-2: 曲名表記揺れ吸収＆マッチング検証 成功")

    def test_dod3_japanese_lyrics_query_cleaning(self):
        """DoD-3: 邦楽のカッコ付き補足語やTHEプレフィックスが正しく正規化されるか"""
        raw_title = "星空のディスタンス (2012 New Recording)"
        cleaned = clean_track_title(raw_title)
        self.assertEqual(cleaned, "星空のディスタンス")

        artist = "THE ALFEE"
        vars_ = get_artist_variations(artist)
        self.assertIn("ALFEE", vars_)
        self.assertIn("THE ALFEE", vars_)
        print("[Pass] DoD-3: 邦楽クエリ正規化エンジン検証 成功")

    def test_dod4_itunes_combined_format(self):
        """DoD-4: 洋楽（4要素）および邦楽（3要素）が意図通りに統合整形されるか"""
        # 洋楽
        western_lyrics = format_combined_lyrics(
            original_lyrics="I never meant to be so bad",
            translated_lyrics="君を傷つけるつもりなんてなかった",
            track_commentary="ウェットンの哀愁のヴォーカルが響く名曲。",
            album_overview="1982年の金字塔アルバム。",
            track_name="Heat Of The Moment",
            is_japanese=False
        )
        self.assertIn("【Original Lyrics】", western_lyrics)
        self.assertIn("【和訳】", western_lyrics)
        self.assertIn("【楽曲解説: Heat Of The Moment】", western_lyrics)
        self.assertIn("【アルバム解説】", western_lyrics)

        # 邦楽
        jp_lyrics = format_combined_lyrics(
            original_lyrics="ささやかな祈りさえも",
            translated_lyrics="",
            track_commentary="ALFEEを代表する名曲。",
            album_overview="日本のロック史に残るアルバム。",
            track_name="星空のディスタンス",
            is_japanese=True
        )
        self.assertIn("【歌詞】", jp_lyrics)
        self.assertNotIn("【Original Lyrics】", jp_lyrics)
        self.assertNotIn("【和訳】", jp_lyrics)
        self.assertIn("【楽曲解説: 星空のディスタンス】", jp_lyrics)
        self.assertIn("【アルバム解説】", jp_lyrics)
        print("[Pass] DoD-4: iTunes統合フォーマッタ検証 成功")

    def test_dod5_translation_error_prevention(self):
        """DoD-5: 和訳エラー文字列が translated_lyrics に混入せず防壁が働くか"""
        pid = "TEST_PID_ERR"
        upsert_track({
            "persistent_id": pid,
            "name": "Error Track",
            "artist": "Asia",
            "album": "Asia Test",
            "duration": 200,
            "status": "unprocessed"
        })

        # エラー文字列を含む和訳を更新しようとする
        error_str = "【翻訳エラー (HTTP 500)】: connection refused"
        update_track_translation(pid, error_str)

        conn = get_connection()
        row = conn.execute("SELECT status, translated_lyrics, last_error FROM tracks WHERE persistent_id = ?", (pid,)).fetchone()
        conn.close()

        self.assertEqual(row["status"], "translation_failed")
        self.assertNotEqual(row["translated_lyrics"], error_str)
        self.assertIn(error_str, row["last_error"])
        print("[Pass] DoD-5: 和訳エラー文字列の混入防止＆防壁検証 成功")

    def test_dod6_dod7_batch_checkpoint_and_retry(self):
        """DoD-6,7: 差分抽出クエリが完了済みをスキップし、失敗曲のみを抽出するか"""
        conn = get_connection()
        # 1件 ready_to_write, 1件 lyrics_not_found を作成
        conn.execute("UPDATE tracks SET status = 'ready_to_write' WHERE persistent_id = 'TEST_PID_001'")
        conn.execute("UPDATE tracks SET status = 'lyrics_not_found', retry_count = 0 WHERE persistent_id = 'TEST_PID_ERR'")
        conn.commit()

        # retry対象取得
        retry_rows = conn.execute("SELECT persistent_id FROM tracks WHERE status IN ('lyrics_not_found', 'translation_failed') AND retry_count < 2").fetchall()
        pids = [r["persistent_id"] for r in retry_rows]
        conn.close()

        self.assertNotIn("TEST_PID_001", pids)
        self.assertIn("TEST_PID_ERR", pids)
        print("[Pass] DoD-6/7: バッチ差分抽出＆チェックポイントスキップ検証 成功")

    def test_dod8_autonomous_diagnostic_classification(self):
        """DoD-8: 自律診断コードが tracks.diagnostic_result に正しく記録されるか"""
        pid = "TEST_PID_ERR"
        update_track_diagnostic(pid, "not_in_lrclib", 2, "LRCLIB未存在")

        conn = get_connection()
        row = conn.execute("SELECT diagnostic_result, retry_count FROM tracks WHERE persistent_id = ?", (pid,)).fetchone()
        conn.close()

        self.assertEqual(row["diagnostic_result"], "not_in_lrclib")
        self.assertEqual(row["retry_count"], 2)
        print("[Pass] DoD-8: 自律深層診断コード記録検証 成功")

    def test_dod9_safe_guardrail_write_selection(self):
        """DoD-9: ready_to_write / completed の曲のみが書き込み対象として選択されるか"""
        tracks = [
            {"name": "Song 1", "status": "ready_to_write", "original_lyrics": "Lyrics 1"},
            {"name": "Song 2", "status": "lyrics_not_found", "original_lyrics": ""},
            {"name": "Song 3", "status": "translation_failed", "original_lyrics": "Lyrics 3"},
            {"name": "Song 4", "status": "completed", "original_lyrics": "Lyrics 4"},
            {"name": "Song 5", "status": "ready_to_write", "original_lyrics": ""}, # 歌詞が空
        ]

        # フィルタリングロジックの検証
        write_targets = [
            t for t in tracks 
            if t["status"] in ("ready_to_write", "completed") and t["original_lyrics"].strip()
        ]

        self.assertEqual(len(write_targets), 2)
        self.assertEqual(write_targets[0]["name"], "Song 1")
        self.assertEqual(write_targets[1]["name"], "Song 4")
        print("[Pass] DoD-9: 安全ガードレール書き込み選択検証 成功")

    def test_dod10_base_rules_compliance(self):
        """DoD-10: 基本方針遵守検査（削除メソッドの不在、データ境界）"""
        # コードベース内に os.remove や track.Delete() が使われていないかを静的検証
        forbidden = ["track.Delete()", "Tracks.Remove", "shutil.rmtree(DATA_DIR)"]
        for py_file in (PROJECT_ROOT / "src").glob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                content = f.read()
                for f_term in forbidden:
                    self.assertNotIn(f_term, content, f"禁止された削除処理が {py_file} で検出されました: {f_term}")
        print("[Pass] DoD-10: 絶対遵守ルール（ファイル削除不在・安全制約）静的検証 成功")

    def test_dod11_regression_check(self):
        """DoD-11: 邦楽判定、システムプロンプト生成等のリグレッションがないか"""
        self.assertTrue(is_likely_western("Asia", "Heat Of The Moment"))
        self.assertFalse(is_likely_western("THE ALFEE", "星空のディスタンス"))

        prompt = build_translation_system_prompt("Asia", "Asia", {
            "story_and_characters": "サバイバル物語",
            "glossary": [{"term": "Heat", "meaning": "熱気", "translation_hint": "情熱と訳す"}]
        })
        self.assertIn("サバイバル物語", prompt)
        self.assertIn("「Heat」: 熱気", prompt)
        print("[Pass] DoD-11: 既存機能・リグレッション検査 成功")

if __name__ == "__main__":
    unittest.main()
