import sqlite3
import hashlib
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

from src.config import DB_PATH, assert_safe_path, ensure_data_directories

def get_connection() -> sqlite3.Connection:
    """SQLiteデータベースへの接続を取得する"""
    ensure_data_directories()
    assert_safe_path(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

def _add_column_if_not_exists(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    """テーブルにカラムが存在しない場合に追加する"""
    col_name = column_def.split()[0]
    cursor = conn.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if col_name not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")

def init_db() -> None:
    """DBスキーマを初期化およびマイグレーションする (v2.1準拠)"""
    conn = get_connection()
    with conn:
        # トラックテーブル
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                persistent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                artist TEXT NOT NULL,
                album TEXT NOT NULL,
                year INTEGER,
                track_number INTEGER,
                disc_number INTEGER,
                duration INTEGER,
                has_original_lyrics INTEGER DEFAULT 0,
                original_lyrics TEXT,
                translated_lyrics TEXT,
                liner_notes TEXT,
                language TEXT DEFAULT 'unknown',
                status TEXT DEFAULT 'unprocessed',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # v2.1 スキーマ拡張カラムの追加 (安全マイグレーション)
        _add_column_if_not_exists(conn, "tracks", "retry_count INTEGER DEFAULT 0")
        _add_column_if_not_exists(conn, "tracks", "last_error TEXT")
        _add_column_if_not_exists(conn, "tracks", "error_phase TEXT")
        _add_column_if_not_exists(conn, "tracks", "diagnostic_result TEXT")
        _add_column_if_not_exists(conn, "tracks", "lyrics_source TEXT")

        # アルバムテーブル
        conn.execute("""
            CREATE TABLE IF NOT EXISTS albums (
                album_id TEXT PRIMARY KEY,
                artist TEXT NOT NULL,
                album TEXT NOT NULL,
                is_live INTEGER DEFAULT 0,
                liner_notes TEXT,
                context_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # アルバムテーブル拡張
        _add_column_if_not_exists(conn, "albums", "critic_status TEXT DEFAULT 'pending'")
        _add_column_if_not_exists(conn, "albums", "critic_source TEXT")

        # バッチ実行履歴テーブル (トレーサビリティ)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS batch_runs (
                run_id TEXT PRIMARY KEY,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                pass_no INTEGER,
                target_count INTEGER,
                success_count INTEGER,
                failed_count INTEGER,
                report_path TEXT
            )
        """)

        # 翻訳メモリテーブル
        conn.execute("""
            CREATE TABLE IF NOT EXISTS translation_memory (
                lyrics_hash TEXT PRIMARY KEY,
                artist TEXT,
                title TEXT,
                original_lyrics TEXT,
                translated_lyrics TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # インデックス
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album, artist)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_retry ON tracks(retry_count)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tm_artist_title ON translation_memory(artist, title)")

        # 既存データのステータス移行マイグレーション (v2.1ステータス体系統一)
        # 1. 和訳欄にエラー文字列を含むものは translation_failed に移行
        conn.execute("""
            UPDATE tracks
            SET status = 'translation_failed', last_error = translated_lyrics, translated_lyrics = ''
            WHERE translated_lyrics LIKE '%【翻訳エラー%'
        """)

        # 2. 歌詞が存在し（かつ和訳済みまたは邦楽で）、エラーのない旧ステータスは ready_to_write に移行
        conn.execute("""
            UPDATE tracks
            SET status = 'ready_to_write'
            WHERE status IN ('translated', 'lyrics_found')
              AND original_lyrics IS NOT NULL AND original_lyrics != ''
              AND (language = 'ja' OR (translated_lyrics IS NOT NULL AND translated_lyrics != '' AND translated_lyrics NOT LIKE '%【翻訳エラー%'))
        """)

        # 3. 歌詞が空のものは unprocessed または lyrics_not_found に整理
        conn.execute("""
            UPDATE tracks
            SET status = 'unprocessed'
            WHERE status = 'lyrics_found' AND (original_lyrics IS NULL OR original_lyrics = '')
        """)

    conn.close()

def compute_lyrics_hash(lyrics: str) -> str:
    """空白や改行を正規化してハッシュを計算する"""
    normalized = "".join(lyrics.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

# ==============================================================================
# トラック操作
# ==============================================================================
def upsert_track(track_data: Dict[str, Any]) -> None:
    conn = get_connection()
    safe_data = {
        "persistent_id": track_data.get("persistent_id"),
        "name": track_data.get("name", "Unknown"),
        "artist": track_data.get("artist", "Unknown"),
        "album": track_data.get("album", "Unknown"),
        "year": track_data.get("year", 0),
        "track_number": track_data.get("track_number", 0),
        "disc_number": track_data.get("disc_number", 1),
        "duration": track_data.get("duration", 0),
        "has_original_lyrics": track_data.get("has_original_lyrics", 0),
        "original_lyrics": track_data.get("original_lyrics", ""),
        "language": track_data.get("language", "unknown"),
        "status": track_data.get("status", "unprocessed"),
    }
    with conn:
        conn.execute("""
            INSERT INTO tracks (
                persistent_id, name, artist, album, year, track_number, disc_number,
                duration, has_original_lyrics, original_lyrics, language, status, updated_at
            ) VALUES (
                :persistent_id, :name, :artist, :album, :year, :track_number, :disc_number,
                :duration, :has_original_lyrics, :original_lyrics, :language, :status, CURRENT_TIMESTAMP
            )
            ON CONFLICT(persistent_id) DO UPDATE SET
                name = excluded.name,
                artist = excluded.artist,
                album = excluded.album,
                year = excluded.year,
                track_number = excluded.track_number,
                disc_number = excluded.disc_number,
                duration = excluded.duration,
                has_original_lyrics = excluded.has_original_lyrics,
                original_lyrics = COALESCE(excluded.original_lyrics, tracks.original_lyrics),
                updated_at = CURRENT_TIMESTAMP
        """, safe_data)
    conn.close()

def update_track_lyrics(persistent_id: str, original_lyrics: str, source: str = "lrclib_get", status: str = "lyrics_found") -> None:
    """歌詞を更新し、取得元を記録する (後方互換対応)"""
    new_status = status if original_lyrics else "lyrics_not_found"
    conn = get_connection()
    with conn:
        conn.execute("""
            UPDATE tracks
            SET original_lyrics = ?,
                lyrics_source = ?,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE persistent_id = ?
        """, (original_lyrics, source, new_status, persistent_id))
    conn.close()

def update_track_lyrics_failed(persistent_id: str, error_msg: str) -> None:
    """歌詞取得失敗を記録する"""
    conn = get_connection()
    with conn:
        conn.execute("""
            UPDATE tracks
            SET status = 'lyrics_not_found',
                last_error = ?,
                error_phase = 'lyrics',
                updated_at = CURRENT_TIMESTAMP
            WHERE persistent_id = ?
        """, (error_msg, persistent_id))
    conn.close()

def update_track_translation(persistent_id: str, translated_lyrics: str, liner_notes: Optional[str] = None, status: str = "ready_to_write") -> None:
    """和訳を更新する。エラー文字列が含まれる場合は拒否して translation_failed に倒す"""
    if "【翻訳エラー" in translated_lyrics:
        update_track_translation_failed(persistent_id, translated_lyrics)
        return

    conn = get_connection()
    with conn:
        conn.execute("""
            UPDATE tracks
            SET translated_lyrics = ?,
                liner_notes = COALESCE(?, liner_notes),
                status = ?,
                last_error = NULL,
                error_phase = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE persistent_id = ?
        """, (translated_lyrics, liner_notes, status, persistent_id))
    conn.close()

def update_track_translation_failed(persistent_id: str, error_msg: str) -> None:
    """和訳失敗を記録する (エラー文字列は和訳カラムには入れず last_error に退避)"""
    conn = get_connection()
    with conn:
        conn.execute("""
            UPDATE tracks
            SET status = 'translation_failed',
                last_error = ?,
                error_phase = 'translation',
                updated_at = CURRENT_TIMESTAMP
            WHERE persistent_id = ?
        """, (error_msg, persistent_id))
    conn.close()

def update_track_liner_notes(persistent_id: str, liner_notes: str) -> None:
    """楽曲個別の解説（ライナーノーツ）を更新する (v2.0/v2.1仕様)"""
    conn = get_connection()
    with conn:
        conn.execute("""
            UPDATE tracks
            SET liner_notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE persistent_id = ?
        """, (liner_notes, persistent_id))
    conn.close()

def update_track_diagnostic(persistent_id: str, diag_result: str, retry_count: int, last_error: Optional[str] = None) -> None:
    """自律診断結果とリトライ回数を更新する"""
    conn = get_connection()
    with conn:
        conn.execute("""
            UPDATE tracks
            SET diagnostic_result = ?,
                retry_count = ?,
                last_error = COALESCE(?, last_error),
                updated_at = CURRENT_TIMESTAMP
            WHERE persistent_id = ?
        """, (diag_result, retry_count, last_error, persistent_id))
    conn.close()

def update_track_completed(persistent_id: str) -> None:
    """iTunes書き込み完了を記録する"""
    conn = get_connection()
    with conn:
        conn.execute("""
            UPDATE tracks
            SET status = 'completed', updated_at = CURRENT_TIMESTAMP
            WHERE persistent_id = ?
        """, (persistent_id,))
    conn.close()

# ==============================================================================
# アルバム操作
# ==============================================================================
def get_album_list() -> List[Dict[str, Any]]:
    """アルバム一覧とその処理状況サマリーを取得する"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT 
            t.artist, 
            t.album, 
            COUNT(t.persistent_id) as track_count,
            SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) as completed_count,
            SUM(CASE WHEN t.status = 'ready_to_write' THEN 1 ELSE 0 END) as ready_count,
            SUM(CASE WHEN t.status = 'lyrics_not_found' THEN 1 ELSE 0 END) as lyrics_not_found_count,
            SUM(CASE WHEN t.status = 'translation_failed' THEN 1 ELSE 0 END) as trans_failed_count,
            SUM(CASE WHEN t.original_lyrics IS NOT NULL AND t.original_lyrics != '' THEN 1 ELSE 0 END) as lyrics_count,
            a.is_live,
            a.liner_notes,
            a.context_json,
            a.critic_status
        FROM tracks t
        LEFT JOIN albums a ON a.album_id = (t.artist || ':' || t.album)
        GROUP BY t.artist, t.album
        ORDER BY t.artist, t.album
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_album_tracks(artist: str, album: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM tracks
        WHERE artist = ? AND album = ?
        ORDER BY disc_number, track_number, name
    """, (artist, album)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def upsert_album_context(artist: str, album: str, liner_notes: str, context_json: str, is_live: bool = False, critic_source: str = "agent", critic_status: str = "done") -> None:
    album_id = f"{artist}:{album}"
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO albums (album_id, artist, album, is_live, liner_notes, context_json, critic_source, critic_status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(album_id) DO UPDATE SET
                is_live = excluded.is_live,
                liner_notes = excluded.liner_notes,
                context_json = excluded.context_json,
                critic_source = excluded.critic_source,
                critic_status = excluded.critic_status,
                updated_at = CURRENT_TIMESTAMP
        """, (album_id, artist, album, 1 if is_live else 0, liner_notes, context_json, critic_source, critic_status))
    conn.close()

# ==============================================================================
# バッチ差分抽出クエリ (v2.1 Pass 1〜3用)
# ==============================================================================
def get_pending_albums() -> List[Dict[str, Any]]:
    """解説（Phase 1）未完了、またはトラックに未完了曲が残っているアルバム一覧"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT t.artist, t.album, a.critic_status, a.liner_notes
        FROM tracks t
        LEFT JOIN albums a ON a.album_id = (t.artist || ':' || t.album)
        WHERE a.critic_status IS NULL 
           OR a.critic_status != 'done'
           OR t.status NOT IN ('ready_to_write', 'completed')
        ORDER BY t.artist, t.album
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_failed_tracks_for_retry() -> List[Dict[str, Any]]:
    """Pass 2用: 1回失敗し、リトライ対象となるトラック一覧"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM tracks
        WHERE status IN ('lyrics_not_found', 'translation_failed')
          AND retry_count < 2
        ORDER BY artist, album, track_number
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_persistently_failed_tracks() -> List[Dict[str, Any]]:
    """Pass 3用: 2回連続で失敗した自律深層診断対象トラック一覧"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM tracks
        WHERE status IN ('lyrics_not_found', 'translation_failed')
          AND retry_count >= 2
        ORDER BY artist, album, track_number
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ==============================================================================
# 翻訳メモリ操作 (重複曲の再利用)
# ==============================================================================
def get_cached_translation(lyrics: str) -> Optional[str]:
    """英語歌詞のハッシュから、過去の和訳を取得する"""
    l_hash = compute_lyrics_hash(lyrics)
    conn = get_connection()
    row = conn.execute("SELECT translated_lyrics FROM translation_memory WHERE lyrics_hash = ?", (l_hash,)).fetchone()
    conn.close()
    return row["translated_lyrics"] if row else None

def save_translation_memory(artist: str, title: str, original_lyrics: str, translated_lyrics: str) -> None:
    """翻訳結果をメモリに保存する"""
    if not translated_lyrics or "【翻訳エラー" in translated_lyrics:
        return
    l_hash = compute_lyrics_hash(original_lyrics)
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO translation_memory (lyrics_hash, artist, title, original_lyrics, translated_lyrics, created_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(lyrics_hash) DO UPDATE SET
                translated_lyrics = excluded.translated_lyrics,
                created_at = CURRENT_TIMESTAMP
        """, (l_hash, artist, title, original_lyrics, translated_lyrics))
    conn.close()

def search_translation_by_title(artist: str, title: str) -> Optional[Dict[str, Any]]:
    """同名曲の過去翻訳を検索する（サジェスト用）"""
    conn = get_connection()
    row = conn.execute("""
        SELECT * FROM translation_memory
        WHERE artist = ? AND title = ?
        ORDER BY created_at DESC LIMIT 1
    """, (artist, title)).fetchone()
    conn.close()
    return dict(row) if row else None
