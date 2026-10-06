"""
インストゥルメンタル曲 自動判定・除外リスト生成モジュール (v2.2仕様)
- 曲名キーワード判定 (Instrumental, Overture, Concerto, 組曲, 等)
- アーティスト特性判定 (The Enid, THE BLACK MAGES, Budapest Strings, Christophe Beck 等)
- 外部ソース判定連携 (Genius is_instrumental 属性)
- 成果物レポート生成 (X:\\LylicData\\reports\\instrumental_tracks_{RunID}.md / .csv)
- DBステータス遷移 (status = 'instrumental')
"""

import os
import re
import csv
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass

from src.config import BASE_DATA_DIR, assert_safe_path, ensure_data_directories
from src.db import get_connection
from src.secondary_sources import fetch_genius_lyrics

logger = logging.getLogger(__name__)

REPORTS_DIR = BASE_DATA_DIR / "reports"

# ==============================================================================
# 判定ルール定義 (4.8.2.2 項準拠)
# ==============================================================================

# 1. 曲名キーワードパターン (大文字小文字不問・単語境界または部分一致)
KEYWORD_PATTERNS = [
    # 英語系 (単語境界)
    (r"\b(?:instrumental|inst|inst\.)\b", "キーワード: Instrumental"),
    (r"\b(?:overture)\b", "キーワード: Overture"),
    (r"\b(?:concerto)\b", "キーワード: Concerto"),
    (r"\b(?:symphony)\b", "キーワード: Symphony"),
    (r"\b(?:sonata)\b", "キーワード: Sonata"),
    (r"\b(?:allegro)\b", "キーワード: Allegro"),
    (r"\b(?:andante)\b", "キーワード: Andante"),
    (r"\b(?:adagio)\b", "キーワード: Adagio"),
    (r"\b(?:prelude)\b", "キーワード: Prelude"),
    (r"\b(?:interlude)\b", "キーワード: Interlude"),
    (r"\b(?:suite)\b", "キーワード: Suite"),
    (r"\b(?:nocturne)\b", "キーワード: Nocturne"),
    (r"\b(?:etude)\b", "キーワード: Etude"),
    (r"\b(?:waltz)\b", "キーワード: Waltz"),
    (r"\b(?:toccata)\b", "キーワード: Toccata"),
    (r"\b(?:rhapsody)\b", "キーワード: Rhapsody"),
    (r"\b(?:soundtrack|score|ost|bgm)\b", "キーワード: サウンドトラック/BGM"),
    # 日本語系
    (r"インスト(?:ゥルメンタル)?", "キーワード: インスト"),
    (r"オフボーカル|off\s*vocal", "キーワード: オフボーカル"),
    (r"カラオケ|karaoke", "キーワード: カラオケ"),
    (r"組曲", "キーワード: 組曲"),
    (r"協奏曲", "キーワード: 協奏曲"),
    (r"交響曲", "キーワード: 交響曲"),
    (r"序曲", "キーワード: 序曲"),
    (r"間奏曲", "キーワード: 間奏曲"),
    (r"前奏曲", "キーワード: 前奏曲"),
    (r"夜想曲", "キーワード: 夜想曲"),
    (r"練習曲", "キーワード: 練習曲"),
    (r"円舞曲", "キーワード: 円舞曲"),
    (r"狂詩曲", "キーワード: 狂詩曲"),
]

# 2. アーティスト特性リスト
ARTIST_HEURISTICS = [
    # シンフォニック・ロック・インスト専用バンド
    ("the enid", "インスト専用バンド特性: The Enid"),
    ("liquid tension experiment", "インスト専用バンド特性: Liquid Tension Experiment"),
    ("steve morse band", "インスト専用バンド特性: Steve Morse Band"),
    ("brand x", "インスト専用バンド特性: Brand X"),
    ("dixie dregs", "インスト専用バンド特性: Dixie Dregs"),
    
    # ゲーム音楽インストアレンジ
    ("the black mages", "ゲーム音楽インストアレンジ: THE BLACK MAGES"),
    ("nobuo uematsu", "ゲーム音楽インストアレンジ: 植松伸夫"),
    ("植松伸夫", "ゲーム音楽インストアレンジ: 植松伸夫"),
    ("yuzo koshiro", "ゲーム音楽インストアレンジ: 古代祐三"),
    ("古代祐三", "ゲーム音楽インストアレンジ: 古代祐三"),
    ("motoi sakuraba", "ゲーム音楽インストアレンジ: 桜庭統"),
    ("桜庭統", "ゲーム音楽インストアレンジ: 桜庭統"),
    ("kenji ito", "ゲーム音楽インストアレンジ: 伊藤賢治"),
    ("伊藤賢治", "ゲーム音楽インストアレンジ: 伊藤賢治"),
    ("yasunori mitsuda", "ゲーム音楽インストアレンジ: 光田康典"),
    ("光田康典", "ゲーム音楽インストアレンジ: 光田康典"),
    ("koichi sugiyama", "ゲーム音楽インストアレンジ: すぎやまこういち"),
    ("すぎやまこういち", "ゲーム音楽インストアレンジ: すぎやまこういち"),

    # オーケストラ・室内管弦楽団・クラシック指揮者
    ("budapest strings", "クラシック管弦楽団特性: Budapest Strings"),
    ("berliner kammerorchester", "クラシック管弦楽団特性: Berliner Kammerorchester"),
    ("helmut winschermann", "クラシック指揮者特性: Helmut Winschermann"),
    ("herbert von karajan", "クラシック指揮者特性: Herbert von Karajan"),
    ("leonard bernstein", "クラシック指揮者特性: Leonard Bernstein"),
    ("london philharmonic orchestra", "クラシック管弦楽団特性: London Philharmonic Orchestra"),
    ("berliner philharmoniker", "クラシック管弦楽団特性: Berliner Philharmoniker"),
    ("wiener philharmoniker", "クラシック管弦楽団特性: Wiener Philharmoniker"),
    ("academy of st. martin in the fields", "クラシック管弦楽団特性: Academy of St. Martin in the Fields"),
    ("neville marriner", "クラシック指揮者特性: Neville Marriner"),
    ("i musici", "クラシック合奏団特性: I Musici"),
    ("english chamber orchestra", "クラシック管弦楽団特性: English Chamber Orchestra"),
    ("trevor pinnock", "クラシック指揮者特性: Trevor Pinnock"),
    ("the english concert", "クラシック管弦楽団特性: The English Concert"),

    # 劇伴・サウンドトラック専門作曲家
    ("christophe beck", "劇伴・サントラ専門作曲家: Christophe Beck"),
    ("john williams", "劇伴・サントラ専門作曲家: John Williams"),
    ("hans zimmer", "劇伴・サントラ専門作曲家: Hans Zimmer"),
    ("ennio morricone", "劇伴・サントラ専門作曲家: Ennio Morricone"),
    ("howard shore", "劇伴・サントラ専門作曲家: Howard Shore"),
    ("joe hisaishi", "劇伴・サントラ専門作曲家: 久石譲"),
    ("久石譲", "劇伴・サントラ専門作曲家: 久石譲"),
    ("hiroyuki sawano", "劇伴・サントラ専門作曲家: 澤野弘之"),
    ("澤野弘之", "劇伴・サントラ専門作曲家: 澤野弘之"),
    ("yuki kajiura", "劇伴・サントラ専門作曲家: 梶浦由記"),
    ("梶浦由記", "劇伴・サントラ専門作曲家: 梶浦由記"),
    ("toshihiko sahashi", "劇伴・サントラ専門作曲家: 佐橋俊彦"),
    ("佐橋俊彦", "劇伴・サントラ専門作曲家: 佐橋俊彦"),
    ("shirō sagisu", "劇伴・サントラ専門作曲家: 鷺巣詩郎"),
    ("鷺巣詩郎", "劇伴・サントラ専門作曲家: 鷺巣詩郎"),
]


@dataclass
class InstrumentalTrackInfo:
    persistent_id: str
    artist: str
    album: str
    title: str
    reason: str
    new_status: str = "instrumental"


def detect_by_keywords(title: str) -> Optional[str]:
    """曲名キーワードによるインスト判定"""
    if not title:
        return None
    for pattern, reason in KEYWORD_PATTERNS:
        if re.search(pattern, title, flags=re.IGNORECASE):
            return reason
    return None


def detect_by_artist(artist: str) -> Optional[str]:
    """アーティスト特性によるインスト判定"""
    if not artist:
        return None
    artist_lower = artist.lower()
    for art_key, reason in ARTIST_HEURISTICS:
        if art_key in artist_lower:
            return reason
    return None


def detect_by_external_genius(artist: str, title: str) -> Optional[str]:
    """Genius API / キャッシュによるインスト判定"""
    try:
        lyrics, is_inst, err = fetch_genius_lyrics(artist, title, use_cache=True)
        if is_inst:
            return "Genius: This song is an instrumental"
    except Exception as e:
        logger.debug(f"Genius external check skipped for {artist} - {title}: {e}")
    return None


def detect_single_track(
    persistent_id: str,
    artist: str,
    album: str,
    title: str,
    check_external: bool = False
) -> Optional[InstrumentalTrackInfo]:
    """
    1曲に対する多層ハイブリッド判定。
    戻り値: InstrumentalTrackInfo (インスト判定時) または None
    """
    # 1. 曲名キーワード判定
    reason = detect_by_keywords(title)
    if reason:
        return InstrumentalTrackInfo(persistent_id, artist, album, title, reason)

    # 2. アーティスト特性判定
    reason = detect_by_artist(artist)
    if reason:
        return InstrumentalTrackInfo(persistent_id, artist, album, title, reason)

    # 3. 外部ソース (Genius) 判定 (指定時のみ、またはキャッシュ済み時)
    if check_external:
        reason = detect_by_external_genius(artist, title)
        if reason:
            return InstrumentalTrackInfo(persistent_id, artist, album, title, reason)

    return None


# ==============================================================================
# レポート出力 (X:\LylicData\reports\)
# ==============================================================================

def export_instrumental_reports(
    detected_tracks: List[InstrumentalTrackInfo],
    run_id: str,
    total_scanned: int
) -> Tuple[Path, Path]:
    """
    オーナー目視確認用の Markdown および CSV レポートを出力する。
    戻り値: (md_path, csv_path)
    """
    ensure_data_directories()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    assert_safe_path(REPORTS_DIR)

    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    md_path = REPORTS_DIR / f"instrumental_tracks_{run_id}.md"
    csv_path = REPORTS_DIR / f"instrumental_tracks_{run_id}.csv"

    assert_safe_path(md_path)
    assert_safe_path(csv_path)

    # 1. Markdown レポート
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# インストゥルメンタル楽曲 判定レポート (v2.2)\n\n")
        f.write(f"- **実行ID**: `{run_id}`\n")
        f.write(f"- **判定日時**: `{timestamp_str}`\n")
        f.write(f"- **スキャン総曲数**: {total_scanned} 曲\n")
        f.write(f"- **インスト判定数**: {len(detected_tracks)} 曲\n\n")
        f.write("> **【概要】** 本リストの楽曲は、歌詞が存在しないインストゥルメンタル曲（クラシック、劇伴、ゲーム音楽、インスト専用バンド、曲名表記等）として自動判定されました。\n")
        f.write("> iTunes書き込み時には歌詞欄を自動省略し、端麗な楽曲解説・アルバム解説のみが反映されます。\n\n")
        f.write("| トラックID (PID) | アーティスト名 | アルバム名 | 曲名 | 判定根拠 | 新ステータス |\n")
        f.write("| :--- | :--- | :--- | :--- | :--- | :--- |\n")
        for t in detected_tracks:
            f.write(f"| `{t.persistent_id}` | {t.artist} | {t.album} | {t.title} | {t.reason} | `{t.new_status}` |\n")

    # 2. CSV レポート
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["persistent_id", "artist", "album", "title", "reason", "new_status"])
        for t in detected_tracks:
            writer.writerow([t.persistent_id, t.artist, t.album, t.title, t.reason, t.new_status])

    return md_path, csv_path


# ==============================================================================
# DBステータス適用
# ==============================================================================

def apply_instrumental_status_to_db(detected_tracks: List[InstrumentalTrackInfo]) -> int:
    """
    判定されたトラックのステータスを 'instrumental' に更新する。
    戻り値: 更新件数
    """
    if not detected_tracks:
        return 0

    conn = get_connection()
    updated_count = 0
    with conn:
        for t in detected_tracks:
            cursor = conn.execute(
                """
                UPDATE tracks
                SET status = 'instrumental',
                    diagnostic_result = 'instrumental',
                    updated_at = CURRENT_TIMESTAMP
                WHERE persistent_id = ?
                """,
                (t.persistent_id,)
            )
            updated_count += cursor.rowcount

    return updated_count


# ==============================================================================
# 一括スキャン・判定オーケストレーター
# ==============================================================================

def scan_and_detect_instrumental_tracks(
    target_pids: Optional[List[str]] = None,
    dry_run: bool = False,
    check_external: bool = False,
    run_id: Optional[str] = None
) -> Tuple[List[InstrumentalTrackInfo], Path, Path, int]:
    """
    未検出曲をスキャンしてインスト曲を判定し、レポートを出力・DB更新する。
    
    引数:
      target_pids: 特定PIDのみを対象とする場合（テスト等用）。None時は lyrics_not_found 全曲
      dry_run: True時はDB更新を行わずレポートのみ出力
      check_external: True時はGenius外部属性チェックも実施
      run_id: 実行ID（None時は自動生成）

    戻り値: (detected_tracks, md_path, csv_path, updated_db_count)
    """
    ensure_data_directories()
    if not run_id:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    conn = get_connection()
    if target_pids:
        placeholders = ",".join("?" for _ in target_pids)
        query = f"SELECT persistent_id, name, artist, album FROM tracks WHERE persistent_id IN ({placeholders})"
        rows = conn.execute(query, target_pids).fetchall()
    else:
        query = "SELECT persistent_id, name, artist, album FROM tracks WHERE status = 'lyrics_not_found'"
        rows = conn.execute(query).fetchall()

    total_scanned = len(rows)
    detected: List[InstrumentalTrackInfo] = []

    for r in rows:
        info = detect_single_track(
            persistent_id=r["persistent_id"],
            artist=r["artist"],
            album=r["album"],
            title=r["name"],
            check_external=check_external
        )
        if info:
            detected.append(info)

    # レポート出力
    md_path, csv_path = export_instrumental_reports(detected, run_id, total_scanned)

    # DB更新
    updated_count = 0
    if not dry_run and detected:
        updated_count = apply_instrumental_status_to_db(detected)

    return detected, md_path, csv_path, updated_count


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="インストゥルメンタル曲 自動判定スクリプト")
    parser.add_argument("--dry-run", action="store_true", help="DB更新を行わずレポート出力のみ実行")
    parser.add_argument("--external", action="store_true", help="Genius外部属性チェックを有効化")
    args = parser.parse_args()

    print(f"=== インストゥルメンタル曲 自動判定開始 (dry_run={args.dry_run}, external={args.external}) ===")
    detected, md_path, csv_path, count = scan_and_detect_instrumental_tracks(
        dry_run=args.dry_run,
        check_external=args.external
    )
    print(f"スキャン完了: 判定件数={len(detected)} 曲, DB更新={count} 件")
    print(f"レポート出力 (Markdown): {md_path}")
    print(f"レポート出力 (CSV):      {csv_path}")
