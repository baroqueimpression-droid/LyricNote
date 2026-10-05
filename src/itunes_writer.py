import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple
import win32com.client

from src.config import BACKUP_DIR, assert_safe_path
from src.db import update_track_completed
from src.poc_helpers import persistent_id, find_track_by_pid, to_file_track

ITUNES_LYRICS_MAX_LENGTH = 32768

def format_combined_lyrics(
    original_lyrics: str,
    translated_lyrics: Optional[str] = None,
    track_commentary: Optional[str] = None,
    album_overview: Optional[str] = None,
    track_name: Optional[str] = None,
    is_japanese: bool = False
) -> str:
    """
    v2.1仕様に基づくiTunes歌詞欄の完全統合フォーマッタ。
    洋楽: 【Original Lyrics】 + 【和訳】 + 【楽曲解説: (曲名)】 + 【アルバム解説】
    邦楽: 【歌詞】 + 【楽曲解説: (曲名)】 + 【アルバム解説】
    ※ 各種解説が存在しない場合はブロックごと自然に省略する。
    """
    parts = []

    # 1. 歌詞ブロック
    has_translation = bool(translated_lyrics and translated_lyrics.strip() and "【日本語楽曲のため和訳スキップ】" not in translated_lyrics)
    
    if is_japanese or not has_translation:
        parts.append("【歌詞】")
        parts.append(original_lyrics.strip())
    else:
        parts.append("【Original Lyrics】")
        parts.append(original_lyrics.strip())
        parts.append("\n【和訳】")
        parts.append(translated_lyrics.strip())

    # 2. 楽曲個別解説ブロック
    if track_commentary and track_commentary.strip():
        header = f"\n【楽曲解説: {track_name}】" if track_name else "\n【楽曲解説】"
        parts.append(header)
        parts.append(track_commentary.strip())

    # 3. アルバム全体解説ブロック
    if album_overview and album_overview.strip():
        parts.append("\n【アルバム解説】")
        parts.append(album_overview.strip())

    return "\n\n".join(parts)

def backup_original_lyrics(track_com, pid: str) -> None:
    """書き込み前に元の歌詞を X:\\LylicData\\lyrics_backup に保存する"""
    assert_safe_path(BACKUP_DIR)
    backup_file = BACKUP_DIR / f"{pid}.json"

    file_track = to_file_track(track_com)
    original_lyrics = ""
    if file_track:
        try:
            original_lyrics = getattr(file_track, "Lyrics", "") or ""
        except Exception:
            pass

    data = {
        "persistent_id": pid,
        "name": getattr(track_com, "Name", ""),
        "artist": getattr(track_com, "Artist", ""),
        "album": getattr(track_com, "Album", ""),
        "original_lyrics": original_lyrics,
        "backed_up_at": datetime.now().isoformat()
    }

    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def write_to_itunes(
    target_pid: str,
    formatted_lyrics: str
) -> Tuple[bool, str]:
    """
    指定されたトラックのLyricsプロパティを更新する。
    絶対ルール: トラック削除やファイル操作は行わず、iTunes COMのLyrics代入のみ行う。
    """
    if len(formatted_lyrics) > ITUNES_LYRICS_MAX_LENGTH:
        return False, f"歌詞テキストがiTunesの文字数制限({ITUNES_LYRICS_MAX_LENGTH}文字)を超過しています: 現在{len(formatted_lyrics)}文字"

    try:
        itunes = win32com.client.Dispatch("iTunes.Application")
    except Exception as e:
        return False, f"iTunes接続エラー: {e}"

    found_track = find_track_by_pid(itunes, target_pid)
    if not found_track:
        return False, f"iTunes内に該当の曲が見つかりませんでした (PID: {target_pid})"

    file_track = to_file_track(found_track)
    if not file_track:
        return False, f"ファイルトラックへのキャストに失敗しました (PID: {target_pid})"

    try:
        # 書き込み前にバックアップ
        backup_original_lyrics(found_track, target_pid)

        # 安全なメタデータ書き込み
        file_track.Lyrics = formatted_lyrics

        # DBステータスを更新
        update_track_completed(target_pid)

        return True, "iTunesへの書き込みが正常に完了しました。"
    except Exception as e:
        return False, f"書き込みエラー: {e}"

def restore_from_backup(target_pid: str) -> Tuple[bool, str]:
    """バックアップから元の歌詞を復元する"""
    assert_safe_path(BACKUP_DIR)
    backup_file = BACKUP_DIR / f"{target_pid}.json"

    if not backup_file.exists():
        return False, "バックアップファイルが存在しません。"

    with open(backup_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    original_lyrics = data.get("original_lyrics", "")

    try:
        itunes = win32com.client.Dispatch("iTunes.Application")
        found_track = find_track_by_pid(itunes, target_pid)
        if not found_track:
            return False, "該当トラックがiTunesで見つかりませんでした。"

        file_track = to_file_track(found_track)
        if not file_track:
            return False, "ファイルトラックへのキャストに失敗しました。"

        file_track.Lyrics = original_lyrics
        return True, "バックアップから歌詞を正常に復元しました。"
    except Exception as e:
        return False, f"復元エラー: {e}"
