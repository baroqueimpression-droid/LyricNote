"""
Task 12: インストゥルメンタル自動判定モジュール 動作検証スクリプト
"""
import sys
import time
from pathlib import Path

from src.instrumental_detector import (
    detect_by_keywords,
    detect_by_artist,
    detect_by_external_genius,
    detect_single_track,
    export_instrumental_reports,
    scan_and_detect_instrumental_tracks,
    InstrumentalTrackInfo,
    REPORTS_DIR
)
from src.config import BASE_DATA_DIR

def run_verification():
    print("=" * 60)
    print("Task 12: インストゥルメンタル判定モジュール 動作検証")
    print("=" * 60)

    # 1. 曲名キーワード判定
    print("\n--- [1] 曲名キーワード判定テスト ---")
    pos_cases = [
        ("Horn Concerto No. 1: Allegro", "キーワード: Concerto"),
        ("R30 Overture", "キーワード: Overture"),
        ("Innocent Love ~Acoustic Guiter Instrumental #1~", "キーワード: Instrumental"),
        ("ホルスト：組曲『惑星』より木星", "キーワード: 組曲"),
        ("交響曲第5番ハ短調 運命", "キーワード: 交響曲"),
    ]
    for title, expected_reason in pos_cases:
        reason = detect_by_keywords(title)
        print(f"  Title: '{title}' -> Reason: '{reason}'")
        assert reason is not None, f"Failed to detect: {title}"
        assert expected_reason.split(":")[0] in reason, f"Reason mismatch: {reason}"

    # 誤検知チェック (ボーカル曲)
    neg_cases = ["Heat of the Moment", "Let It Be", "星空のディスタンス", "Yellow"]
    for title in neg_cases:
        reason = detect_by_keywords(title)
        assert reason is None, f"False positive detected: {title} -> {reason}"
    print("Keyword detection PASS (Positive: 5/5, False-positive: 0/4)")

    # 2. アーティスト特性判定
    print("\n--- [2] アーティスト特性判定テスト ---")
    artist_cases = [
        ("The Enid", "インスト専用バンド特性: The Enid"),
        ("THE BLACK MAGES", "ゲーム音楽インストアレンジ: THE BLACK MAGES"),
        ("Budapest Strings", "クラシック管弦楽団特性: Budapest Strings"),
        ("Helmut Winschermann", "クラシック指揮者特性: Helmut Winschermann"),
        ("Christophe Beck", "劇伴・サントラ専門作曲家: Christophe Beck"),
    ]
    for artist, expected_reason in artist_cases:
        reason = detect_by_artist(artist)
        print(f"  Artist: '{artist}' -> Reason: '{reason}'")
        assert reason is not None, f"Failed to detect artist: {artist}"
        assert reason == expected_reason, f"Reason mismatch: {reason}"

    # 一般ボーカルバンドの誤検知チェック
    for artist in ["Asia", "The Beatles", "THE ALFEE", "Coldplay", "The Tangent"]:
        reason = detect_by_artist(artist)
        assert reason is None, f"False positive artist: {artist} -> {reason}"
    print("Artist heuristics detection PASS (Positive: 5/5, False-positive: 0/5)")

    # 3. 外部ソース (Genius) 属性判定連携
    print("\n--- [3] 外部ソース (Genius) 連携テスト ---")
    reason_inst = detect_by_external_genius("The Flower Kings", "Babylon")
    print(f"  The Flower Kings - Babylon -> External Reason: '{reason_inst}'")
    assert reason_inst == "Genius: This song is an instrumental"

    # ボーカル曲で外部判定されないこと
    reason_vocal = detect_by_external_genius("Asia", "Heat of the Moment")
    assert reason_vocal is None, f"Vocal song marked as instrumental: {reason_vocal}"
    print("External Genius detection PASS")

    # 4. レポート生成機能の検証
    print("\n--- [4] レポート出力検証 (Markdown / CSV) ---")
    sample_tracks = [
        InstrumentalTrackInfo("PID_001", "The Enid", "In The Region Of The Summer Stars", "The Fool", "インスト専用バンド特性: The Enid"),
        InstrumentalTrackInfo("PID_002", "THE BLACK MAGES", "THE BLACK MAGES", "Battle Scene", "ゲーム音楽インストアレンジ: THE BLACK MAGES"),
        InstrumentalTrackInfo("PID_003", "The Flower Kings", "Adam & Eve", "Babylon", "Genius: This song is an instrumental"),
        InstrumentalTrackInfo("PID_004", "Budapest Strings", "Baroque Masterpieces", "Horn Concerto No. 1: Allegro", "キーワード: Concerto"),
    ]
    run_id = f"test_{int(time.time())}"
    md_path, csv_path = export_instrumental_reports(sample_tracks, run_id, total_scanned=10)
    print(f"  MD Report: {md_path} (exists={md_path.exists()})")
    print(f"  CSV Report: {csv_path} (exists={csv_path.exists()})")
    assert md_path.exists()
    assert csv_path.exists()

    # MD レポートの中身確認
    with open(md_path, "r", encoding="utf-8") as f:
        md_content = f.read()
    assert "インスト判定数**: 4 曲" in md_content
    assert "The Enid" in md_content
    assert "Battle Scene" in md_content
    print("Report export PASS")

    # 5. 特定PID指定によるスキャン＆DB判定ドライラン検証
    print("\n--- [5] 特定PIDスキャン＆ドライラン検証 ---")
    detected, r_md, r_csv, count = scan_and_detect_instrumental_tracks(
        target_pids=["PID_001", "PID_002"],
        dry_run=True,
        run_id=f"dry_{int(time.time())}"
    )
    print(f"  Target scanned detected: {len(detected)} tracks, DB updated: {count} tracks (dry-run)")
    assert count == 0, "Dry-run should not update DB"

    print("\n" + "=" * 60)
    print("ALL INSTRUMENTAL DETECTOR VERIFICATION CHECKS PASSED (100%)")
    print("=" * 60)

if __name__ == "__main__":
    run_verification()
