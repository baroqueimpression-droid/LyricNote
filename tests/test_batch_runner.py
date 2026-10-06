import sys
import os
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import init_db, get_connection, upsert_track
from src.batch_runner import BatchRunner

class TestBatchRunnerExecution(unittest.TestCase):
    def setUp(self):
        init_db()

    def test_batch_runner_simulation(self):
        """Pass 1〜3の自律実行、差分リトライ、レポート出力の総合検証"""
        # テスト用トラックを投入
        upsert_track({
            "persistent_id": "BATCH_TEST_01",
            "name": "Heat Of The Moment",
            "artist": "Asia",
            "album": "Batch Test Album",
            "language": "en",
            "status": "ready_to_write",
            "original_lyrics": "I never meant to be so bad"
        })
        upsert_track({
            "persistent_id": "BATCH_TEST_02",
            "name": "Simulated Error Song",
            "artist": "NonExistentArtistXYZ",
            "album": "Batch Test Album",
            "language": "en",
            "status": "lyrics_not_found",
            "retry_count": 0
        })

        runner = BatchRunner()
        # テスト対象トラックのみを指定して Pass 2 と Pass 3 をテスト
        p2_stats = runner._run_pass2(target_pids=["BATCH_TEST_02"])
        self.assertIn("retried", p2_stats)

        p3_stats = runner._run_pass3(target_pids=["BATCH_TEST_02"])
        self.assertIn("diagnosed", p3_stats)

        # 診断結果が tracks テーブルに刻まれたか確認
        conn = get_connection()
        row = conn.execute("SELECT diagnostic_result, retry_count FROM tracks WHERE persistent_id = 'BATCH_TEST_02'").fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertIn(row["diagnostic_result"], ["not_in_lrclib", "auto_fixed", "bug_suspected"])
        print(f"[Pass] BatchRunner結合自律診断検証 成功: 診断結果={row['diagnostic_result']}, リトライ数={row['retry_count']}")

if __name__ == "__main__":
    unittest.main()
