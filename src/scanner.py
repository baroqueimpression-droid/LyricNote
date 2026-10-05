import time
from typing import Callable, Optional, Dict, Any, Generator
import win32com.client
from win32com.client import constants

from src.db import upsert_track, get_connection
from src.poc_helpers import persistent_id, is_likely_western

def scan_itunes_to_db(
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> Dict[str, int]:
    """
    iTunesライブラリを高速スキャンし、SQLite DBに同期する。
    絶対ルール: トラック削除メソッドは一切存在せず、読み取り専用で動作する。
    全曲（邦楽含む）を自動スキャンする。
    """
    try:
        itunes = win32com.client.Dispatch("iTunes.Application")
    except Exception as e:
        raise RuntimeError(f"iTunesへの接続に失敗しました: {e}")

    library = itunes.LibraryPlaylist
    tracks = library.Tracks
    total = tracks.Count

    stats = {
        "total_scanned": 0,
        "added_or_updated": 0,
        "skipped": 0,
        "errors": 0
    }

    # 一括トランザクションで高速化
    conn = get_connection()
    cursor = conn.cursor()

    try:
        for i in range(1, total + 1):
            if i % 100 == 0 or i == total:
                if progress_callback:
                    progress_callback(i, total, f"スキャン中: {i}/{total}")

            try:
                t = tracks.Item(i)
                # KindAsString が音声ファイルであるもののみ対象（Podcastやビデオを除外）
                kind = getattr(t, "KindAsString", "")
                if "オーディオ" not in kind and "audio" not in kind.lower():
                    stats["skipped"] += 1
                    continue

                name = getattr(t, "Name", "") or ""
                artist = getattr(t, "Artist", "") or ""
                album = getattr(t, "Album", "") or ""

                if not name or not artist or not album:
                    stats["skipped"] += 1
                    continue

                lang = "en" if is_likely_western(artist, name) else "ja"

                pid = persistent_id(itunes, t)
                year = getattr(t, "Year", 0) or 0
                track_no = getattr(t, "TrackNumber", 0) or 0
                disc_no = getattr(t, "DiscNumber", 1) or 1
                duration = getattr(t, "Duration", 0) or 0

                # 歌詞の有無（Lyricsプロパティへのアクセスは若干重いが、スキャン時にキャッシュしておくと後が楽）
                # ただしフリーズ防止のため、エラーは握りつぶす
                has_lyrics = 0
                lyrics_text = ""
                try:
                    lyrics_text = getattr(t, "Lyrics", "") or ""
                    if lyrics_text.strip():
                        has_lyrics = 1
                except Exception:
                    pass

                status = "lyrics_found" if has_lyrics else "unprocessed"

                cursor.execute("""
                    INSERT INTO tracks (
                        persistent_id, name, artist, album, year, track_number, disc_number,
                        duration, has_original_lyrics, original_lyrics, language, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(persistent_id) DO UPDATE SET
                        name = excluded.name,
                        artist = excluded.artist,
                        album = excluded.album,
                        year = excluded.year,
                        track_number = excluded.track_number,
                        disc_number = excluded.disc_number,
                        duration = excluded.duration,
                        has_original_lyrics = excluded.has_original_lyrics,
                        original_lyrics = CASE WHEN excluded.original_lyrics != '' THEN excluded.original_lyrics ELSE tracks.original_lyrics END,
                        language = excluded.language,
                        updated_at = CURRENT_TIMESTAMP
                """, (pid, name, artist, album, year, track_no, disc_no, duration, has_lyrics, lyrics_text, lang, status))

                stats["added_or_updated"] += 1

            except Exception as e:
                stats["errors"] += 1
                continue

            stats["total_scanned"] += 1

            # 500件ごとにコミット
            if i % 500 == 0:
                conn.commit()

        conn.commit()
    finally:
        conn.close()

    return stats
