import gc
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.config import BASE_DATA_DIR, assert_safe_path
from src.db import (
    get_connection, get_album_tracks, get_pending_albums,
    update_track_lyrics, update_track_lyrics_failed,
    update_track_translation, update_track_translation_failed,
    update_track_diagnostic, upsert_album_context
)
from src.lyrics_fetcher import fetch_lyrics_multistage
from src.translator import translate_lyrics_safe
from src.critic import validate_critic_output, match_track_commentaries

REPORTS_DIR = BASE_DATA_DIR / "reports"

def ensure_reports_dir() -> Path:
    assert_safe_path(BASE_DATA_DIR)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR

class BatchRunner:
    """
    v2.1仕様: 2パス自動実行 ＋ 自律深層診断オーケストレーター
    """
    def __init__(self, progress_callback: Optional[Callable[[str], None]] = None):
        self.cb = progress_callback or (lambda msg: None)
        ensure_reports_dir()
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]

    def log(self, msg: str):
        try:
            print(f"[BatchRunner] {msg}", flush=True)
        except UnicodeEncodeError:
            print(f"[BatchRunner] {msg.encode('ascii', 'backslashreplace').decode('ascii')}", flush=True)
        self.cb(msg)

    def run_full_pipeline(self) -> Dict[str, Any]:
        """
        Pass 1 (全曲走破) -> Pass 2 (自動差分再実行) -> Pass 3 (自律診断) を無停止で完走する
        """
        self.log(f"=== ライブラリ全自動バッチ開始 [RunID: {self.run_id}] ===")
        start_time = datetime.now()

        # --- Pass 1: 第1走 (全アルバム順次走破) ---
        self.log("\n>>> 【Pass 1】 第1走: 全未完了アルバムのノンストップ処理開始")
        pass1_stats = self._run_pass1()

        # --- Pass 2: 第2走 (差分自動再実行・1回) ---
        self.log("\n>>> 【Pass 2】 第2走: 失敗曲・未完了曲の差分自動リトライ開始")
        pass2_stats = self._run_pass2()

        # --- Pass 3: 第3走 (2回失敗曲の自律深層診断) ---
        self.log("\n>>> 【Pass 3】 第3走: 2回連続失敗曲の自律深層診断＆自己修復開始")
        pass3_stats = self._run_pass3()

        finished_time = datetime.now()
        duration_sec = int((finished_time - start_time).total_seconds())

        report_summary = self._generate_report(start_time, finished_time, duration_sec, pass1_stats, pass2_stats, pass3_stats)
        self.log("\n=== ライブラリ全自動バッチ完了 ===")
        return report_summary

    def _run_pass1(self) -> Dict[str, int]:
        """Pass 1: 未完了アルバムを順次処理"""
        pending_albums = get_pending_albums()
        total_albums = len(pending_albums)
        self.log(f"Pass 1 対象アルバム数: {total_albums} 枚")

        stats = {"albums_processed": 0, "tracks_ready": 0, "tracks_failed": 0}

        for idx, alb in enumerate(pending_albums, 1):
            artist = alb["artist"]
            album = alb["album"]
            self.log(f"[{idx}/{total_albums}] 処理中: {artist} - {album}")

            # アルバム内トラックの処理 (Phase 2)
            tracks = get_album_tracks(artist, album)
            
            # コンテキストJSON（用語集）の取得
            context_dict = None
            conn = get_connection()
            alb_row = conn.execute("SELECT context_json FROM albums WHERE album_id = ?", (f"{artist}:{album}",)).fetchone()
            conn.close()
            if alb_row and alb_row["context_json"]:
                try:
                    context_dict = json.loads(alb_row["context_json"])
                except Exception:
                    pass

            for t in tracks:
                if t["status"] in ("ready_to_write", "completed"):
                    continue

                pid = t["persistent_id"]
                t_name = t["name"]
                t_duration = t["duration"] or 0
                is_ja = (t["language"] == "ja")

                # 1. 歌詞取得
                lyrics = t["original_lyrics"]
                if not lyrics or not lyrics.strip():
                    l_text, l_src, l_err = fetch_lyrics_multistage(artist, t_name, album, t_duration)
                    if l_text:
                        lyrics = l_text
                        update_track_lyrics(pid, l_text, source=l_src or "lrclib", status="ready_to_write" if is_ja else "unprocessed")
                    else:
                        update_track_lyrics_failed(pid, l_err or "not_in_lrclib")
                        stats["tracks_failed"] += 1
                        continue

                # 2. 和訳処理 (洋楽のみ)
                if is_ja:
                    update_track_translation(pid, "", status="ready_to_write")
                    stats["tracks_ready"] += 1
                else:
                    trans_text, _, trans_err = translate_lyrics_safe(artist, t_name, album, lyrics, context_dict)
                    if trans_text is not None:
                        update_track_translation(pid, trans_text, status="ready_to_write")
                        stats["tracks_ready"] += 1
                    else:
                        update_track_translation_failed(pid, trans_err or "Ollama error")
                        stats["tracks_failed"] += 1

            stats["albums_processed"] += 1

            # PC冷却インターバル (2秒) & GC
            time.sleep(2)
            if idx % 10 == 0:
                gc.collect()

        return stats

    def _run_pass2(self, target_pids: Optional[List[str]] = None) -> Dict[str, int]:
        """Pass 2: 1回失敗したトラックを自動差分リトライ"""
        conn = get_connection()
        if target_pids:
            placeholders = ",".join("?" * len(target_pids))
            query = f"""
                SELECT * FROM tracks 
                WHERE status IN ('lyrics_not_found', 'translation_failed')
                  AND persistent_id IN ({placeholders})
                ORDER BY artist, album, track_number
            """
            failed_tracks = conn.execute(query, target_pids).fetchall()
        else:
            query = """
                SELECT * FROM tracks 
                WHERE status IN ('lyrics_not_found', 'translation_failed')
                  AND retry_count < 1
                ORDER BY artist, album, track_number
            """
            failed_tracks = conn.execute(query).fetchall()
        conn.close()

        total = len(failed_tracks)
        self.log(f"Pass 2 差分リトライ対象トラック数: {total} 曲")
        stats = {"retried": 0, "recovered": 0, "still_failed": 0}

        for idx, t in enumerate(failed_tracks, 1):
            pid = t["persistent_id"]
            artist = t["artist"]
            album = t["album"]
            t_name = t["name"]
            is_ja = (t["language"] == "ja")
            status = t["status"]

            self.log(f"Pass 2 リトライ [{idx}/{total}]: {artist} - {t_name} ({status})")

            # retry_count をインクリメント
            conn = get_connection()
            conn.execute("UPDATE tracks SET retry_count = retry_count + 1 WHERE persistent_id = ?", (pid,))
            conn.commit()
            conn.close()
            stats["retried"] += 1

            # 歌詞失敗のリトライ
            if status == "lyrics_not_found":
                l_text, l_src, l_err = fetch_lyrics_multistage(artist, t_name, album, t["duration"] or 0)
                if l_text:
                    update_track_lyrics(pid, l_text, source=l_src or "lrclib", status="ready_to_write" if is_ja else "unprocessed")
                    if is_ja:
                        stats["recovered"] += 1
                        continue
                    else:
                        # 洋楽なら和訳へ進む
                        t_lyrics = l_text
                else:
                    update_track_lyrics_failed(pid, l_err or "not_in_lrclib")
                    stats["still_failed"] += 1
                    continue
            else:
                t_lyrics = t["original_lyrics"]

            # 和訳失敗のリトライ (洋楽)
            if not is_ja and t_lyrics:
                trans_text, _, trans_err = translate_lyrics_safe(artist, t_name, album, t_lyrics)
                if trans_text is not None:
                    update_track_translation(pid, trans_text, status="ready_to_write")
                    stats["recovered"] += 1
                else:
                    update_track_translation_failed(pid, trans_err or "Ollama error retry failed")
                    stats["still_failed"] += 1

            time.sleep(1)

        return stats

    def _run_pass3(self, target_pids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Pass 3: 2回連続失敗トラックの自律深層診断"""
        conn = get_connection()
        if target_pids:
            placeholders = ",".join("?" * len(target_pids))
            query = f"""
                SELECT * FROM tracks 
                WHERE status IN ('lyrics_not_found', 'translation_failed')
                  AND persistent_id IN ({placeholders})
                ORDER BY artist, album, track_number
            """
            persistently_failed = conn.execute(query, target_pids).fetchall()
        else:
            query = """
                SELECT * FROM tracks 
                WHERE status IN ('lyrics_not_found', 'translation_failed')
                ORDER BY artist, album, track_number
            """
            persistently_failed = conn.execute(query).fetchall()
        conn.close()

        total = len(persistently_failed)
        self.log(f"Pass 3 自律深層診断対象トラック数: {total} 曲")

        stats = {
            "diagnosed": 0,
            "auto_fixed": 0,
            "not_in_lrclib": 0,
            "translation_model_error": 0,
            "query_mismatch": 0
        }

        for idx, t in enumerate(persistently_failed, 1):
            pid = t["persistent_id"]
            artist = t["artist"]
            album = t["album"]
            t_name = t["name"]
            status = t["status"]
            last_err = t["last_error"] or ""
            is_ja = (t["language"] == "ja")

            diag_code = "bug_suspected"

            if status == "lyrics_not_found":
                # 歌詞失敗の深層分析: 単純クエリ（曲名のみ等）で救済可能か試行
                simple_title = "".join(c for c in t_name if c.isalnum() or c.isspace()).strip()
                l_text, l_src, _ = fetch_lyrics_multistage(artist, simple_title, album, 0)
                if l_text:
                    # 自己修復成功！
                    update_track_lyrics(pid, l_text, source="self_healed_search", status="ready_to_write" if is_ja else "unprocessed")
                    update_track_diagnostic(pid, "auto_fixed", 2, "自己修復: 単純クエリ検索で救済成功")
                    stats["auto_fixed"] += 1
                    self.log(f"  [自己修復成功] {artist} - {t_name}")
                    continue
                else:
                    diag_code = "not_in_lrclib"
                    stats["not_in_lrclib"] += 1

            elif status == "translation_failed":
                diag_code = "translation_model_error"
                stats["translation_model_error"] += 1

            update_track_diagnostic(pid, diag_code, 2, f"診断完了: {diag_code} (元エラー: {last_err[:100]})")
            stats["diagnosed"] += 1

        return stats

    def _generate_report(
        self, start_time: datetime, finished_time: datetime, duration_sec: int,
        p1: Dict[str, int], p2: Dict[str, int], p3: Dict[str, Any]
    ) -> Dict[str, Any]:
        """最終レポートを生成し、X:\\LylicData\\reports に保存する"""
        conn = get_connection()
        total_tracks = conn.execute("SELECT count(*) as c FROM tracks").fetchone()["c"]
        ready_tracks = conn.execute("SELECT count(*) as c FROM tracks WHERE status = 'ready_to_write'").fetchone()["c"]
        completed_tracks = conn.execute("SELECT count(*) as c FROM tracks WHERE status = 'completed'").fetchone()["c"]
        failed_lyrics = conn.execute("SELECT count(*) as c FROM tracks WHERE status = 'lyrics_not_found'").fetchone()["c"]
        failed_trans = conn.execute("SELECT count(*) as c FROM tracks WHERE status = 'translation_failed'").fetchone()["c"]
        conn.close()

        success_total = ready_tracks + completed_tracks
        success_rate = (success_total / total_tracks * 100) if total_tracks else 0.0

        report_text = f"""======================================================================
  LyricNote ライブラリ全自動バッチ & 自律診断 完了レポート (v2.1)
======================================================================
実行ID: {self.run_id}
開始日時: {start_time.strftime('%Y-%m-%d %H:%M:%S')}
完了日時: {finished_time.strftime('%Y-%m-%d %H:%M:%S')}
所要時間: {duration_sec // 60} 分 {duration_sec % 60} 秒

【全体サマリー】
・総トラック数: {total_tracks} 曲
・書き込み可能 (Ready/Completed): {success_total} 曲 ({success_rate:.1f}%)
  - 今回Ready化: {ready_tracks} 曲
  - すでに書き込み完了済: {completed_tracks} 曲
・残留エラー数: {failed_lyrics + failed_trans} 曲
  - 歌詞未取得: {failed_lyrics} 曲
  - 和訳失敗: {failed_trans} 曲

【Pass 実行実績】
・Pass 1 (全曲走破): アルバム {p1.get('albums_processed', 0)} 枚処理
・Pass 2 (差分リトライ): {p2.get('retried', 0)} 曲再試行 -> {p2.get('recovered', 0)} 曲救済
・Pass 3 (自律診断):
  - 自己修復成功: {p3.get('auto_fixed', 0)} 曲
  - WEB上に歌詞未存在: {p3.get('not_in_lrclib', 0)} 曲
  - 和訳モデル通信異常: {p3.get('translation_model_error', 0)} 曲

【Phase 3 (iTunes書き込み) への案内】
WebUIを開き、プレビュー確認の上「アルバム全曲を一括書き込み」を実行してください。
※ ready_to_write の {ready_tracks} 曲のみが安全にiTunesに書き込まれます（エラー曲は自動除外）。
======================================================================
"""
        report_file = REPORTS_DIR / f"batch_run_{self.run_id}.txt"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report_text)

        # batch_runs テーブルに記録
        conn = get_connection()
        conn.execute("""
            INSERT INTO batch_runs (run_id, started_at, finished_at, pass_no, target_count, success_count, failed_count, report_path)
            VALUES (?, ?, ?, 3, ?, ?, ?, ?)
        """, (self.run_id, start_time.isoformat(), finished_time.isoformat(), total_tracks, success_total, failed_lyrics + failed_trans, str(report_file)))
        conn.commit()
        conn.close()

        return {
            "run_id": self.run_id,
            "total_tracks": total_tracks,
            "success_total": success_total,
            "success_rate": success_rate,
            "failed_total": failed_lyrics + failed_trans,
            "report_path": str(report_file),
            "report_text": report_text
        }

if __name__ == "__main__":
    runner = BatchRunner()
    runner.run_full_pipeline()

