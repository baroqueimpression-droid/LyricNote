"""
タスク11: セカンダリ外部ソース 未検出曲一括再スキャンバッチ
- 対象: library.db 内の status='lyrics_not_found' のトラック
- パイプライン: fetch_secondary_lyrics_multistage (J-Lyric.net / Genius / Lyrics.ovh)
- 安全性: 50曲ごとにチェックポイントコミット、iTunes直接書き込みゼロ
- 成果物: X:\\LylicData\\reports\\secondary_scan_{RunID}.md (.csv)
"""

import sys
import os
import time
import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from src.config import BASE_DATA_DIR, assert_safe_path, ensure_data_directories
from src.db import get_connection
from src.secondary_sources import fetch_secondary_lyrics_multistage, is_japanese_text

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

REPORTS_DIR = BASE_DATA_DIR / "reports"


def run_secondary_scan(checkpoint_interval: int = 50) -> Dict[str, Any]:
    """
    lyrics_not_found の全トラックに対し、セカンダリ外部ソースによる再スキャンを実行する。
    """
    ensure_data_directories()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    assert_safe_path(REPORTS_DIR)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    start_time = time.time()

    conn = get_connection()
    cursor = conn.cursor()

    # 1. 対象曲の抽出
    rows = cursor.execute("""
        SELECT persistent_id, name, artist, album, language
        FROM tracks
        WHERE status = 'lyrics_not_found'
        ORDER BY artist, album, track_number, name
    """).fetchall()

    total_tracks = len(rows)
    logger.info(f"=== セカンダリ外部ソース再スキャン開始 [RunID: {run_id}] ===")
    logger.info(f"対象トラック数: {total_tracks} 曲")

    if total_tracks == 0:
        logger.info("再スキャン対象の未検出曲はありません。")
        return {"run_id": run_id, "total": 0, "rescued": 0, "instrumental": 0}

    rescued_count = 0
    instrumental_count = 0
    not_found_count = 0
    results_detail: List[Dict[str, Any]] = []

    # 2. スキャン実行ループ
    for idx, row in enumerate(rows, 1):
        pid = row["persistent_id"]
        title = row["name"]
        artist = row["artist"]
        album = row["album"]
        lang = row["language"] or "unknown"

        is_ja = (lang == "ja") or is_japanese_text(artist) or is_japanese_text(title)
        req_lang = "ja" if is_ja else "en"

        try:
            lyrics, is_inst, src, err = fetch_secondary_lyrics_multistage(
                artist=artist,
                title=title,
                language=req_lang,
                use_cache=True
            )
        except Exception as e:
            lyrics, is_inst, src, err = None, False, None, f"exception: {e}"

        status_after = "lyrics_not_found"
        diag_after = "not_in_lrclib"

        if lyrics:
            rescued_count += 1
            # 邦楽は和訳不要のため ready_to_write、洋楽は和訳待ちとして unprocessed
            if req_lang == "ja":
                status_after = "ready_to_write"
                diag_after = "rescued_secondary_ja"
            else:
                status_after = "unprocessed"
                diag_after = "rescued_secondary_en"

            cursor.execute("""
                UPDATE tracks
                SET original_lyrics = ?,
                    lyrics_source = ?,
                    status = ?,
                    diagnostic_result = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE persistent_id = ?
            """, (lyrics, src, status_after, diag_after, pid))

            results_detail.append({
                "persistent_id": pid,
                "artist": artist,
                "album": album,
                "title": title,
                "result": "rescued",
                "source": src,
                "lyrics_len": len(lyrics),
                "new_status": status_after
            })
            logger.info(f"[{idx}/{total_tracks}] [救済成功 ({src})] {artist} - {title} ({len(lyrics)}文字) -> {status_after}")

        elif is_inst:
            instrumental_count += 1
            status_after = "instrumental"
            diag_after = "instrumental"

            cursor.execute("""
                UPDATE tracks
                SET status = 'instrumental',
                    diagnostic_result = 'instrumental',
                    lyrics_source = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE persistent_id = ?
            """, (src or "genius", pid))

            results_detail.append({
                "persistent_id": pid,
                "artist": artist,
                "album": album,
                "title": title,
                "result": "instrumental",
                "source": src or "genius",
                "lyrics_len": 0,
                "new_status": "instrumental"
            })
            logger.info(f"[{idx}/{total_tracks}] [インスト検出 ({src})] {artist} - {title} -> instrumental")

        else:
            not_found_count += 1
            # 未検出のまま維持
            results_detail.append({
                "persistent_id": pid,
                "artist": artist,
                "album": album,
                "title": title,
                "result": "not_found",
                "source": None,
                "lyrics_len": 0,
                "new_status": "lyrics_not_found"
            })

        # チェックポイントコミット
        if idx % checkpoint_interval == 0:
            conn.commit()
            elapsed_sec = time.time() - start_time
            avg_per_track = elapsed_sec / idx
            remain_sec = avg_per_track * (total_tracks - idx)
            remain_min = remain_sec / 60
            logger.info(f"--- [進捗チェックポイント {idx}/{total_tracks}] 救済={rescued_count}, インスト={instrumental_count}, 残余予想時間={remain_min:.1f}分 ---")

    # 最終コミット
    conn.commit()
    conn.close()

    total_elapsed = time.time() - start_time
    total_elapsed_min = total_elapsed / 60

    logger.info(f"=== セカンダリ再スキャン完了 ===")
    logger.info(f"総処理数: {total_tracks} 曲 (所要時間: {total_elapsed_min:.1f} 分)")
    logger.info(f"救済数: {rescued_count} 曲, インスト判定数: {instrumental_count} 曲, 残留未検出: {not_found_count} 曲")

    # 3. 成果物レポート生成
    md_path = REPORTS_DIR / f"secondary_scan_{run_id}.md"
    csv_path = REPORTS_DIR / f"secondary_scan_{run_id}.csv"

    assert_safe_path(md_path)
    assert_safe_path(csv_path)

    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# セカンダリ外部ソース再スキャン 完了レポート (タスク11)\n\n")
        f.write(f"- **実行ID**: `{run_id}`\n")
        f.write(f"- **実行日時**: `{timestamp_str}`\n")
        f.write(f"- **スキャン総曲数**: {total_tracks} 曲\n")
        f.write(f"- **所要時間**: {total_elapsed_min:.1f} 分 ({total_elapsed:.0f} 秒)\n\n")
        f.write(f"### 【サマリー】\n")
        f.write(f"- **歌詞救済数**: **{rescued_count} 曲** (J-Lyric.net / Genius / Lyrics.ovh)\n")
        f.write(f"- **インスト検出数**: **{instrumental_count} 曲** (Genius属性)\n")
        f.write(f"- **残留未検出数**: **{not_found_count} 曲**\n\n")
        f.write(f"### 【救済・検出トラック詳細 (先頭200件抜粋)】\n\n")
        f.write(f"| トラックID | アーティスト | アルバム | 曲名 | 結果 | 取得元 | 新ステータス |\n")
        f.write(f"| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n")
        
        rescued_items = [r for r in results_detail if r["result"] in ("rescued", "instrumental")]
        for item in rescued_items[:200]:
            f.write(f"| `{item['persistent_id']}` | {item['artist']} | {item['album']} | {item['title']} | {item['result']} | {item['source']} | `{item['new_status']}` |\n")

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["persistent_id", "artist", "album", "title", "result", "source", "lyrics_len", "new_status"])
        for item in results_detail:
            writer.writerow([
                item["persistent_id"],
                item["artist"],
                item["album"],
                item["title"],
                item["result"],
                item["source"],
                item["lyrics_len"],
                item["new_status"]
            ])

    logger.info(f"Markdown レポート生成: {md_path}")
    logger.info(f"CSV レポート生成:      {csv_path}")

    return {
        "run_id": run_id,
        "total": total_tracks,
        "rescued": rescued_count,
        "instrumental": instrumental_count,
        "not_found": not_found_count,
        "elapsed_min": total_elapsed_min,
        "md_path": str(md_path),
        "csv_path": str(csv_path)
    }


if __name__ == "__main__":
    run_secondary_scan(checkpoint_interval=50)
