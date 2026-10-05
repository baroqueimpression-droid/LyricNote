"""試作(PoC)共通ユーティリティ。

- 歌詞・キャッシュ等のデータは Google Drive 同期外のローカルディスクに保存する(DATA_DIR)。
- 歌詞データは私的利用に限る。共有・公開しないこと。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Windows コンソールで日本語を安全に出力する
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DATA_DIR = Path(os.environ.get("LYLIC_DATA_DIR", r"C:\Users\Baroq\LylicData"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRACKS_CSV = DATA_DIR / "tracks_scan.csv"
LRCLIB_CACHE_DIR = DATA_DIR / "lrclib_cache"
BACKUP_DIR = DATA_DIR / "lyrics_backup"
TRANSLATE_OUT_DIR = DATA_DIR / "translate_compare"

# iTunes COM 定数 (ITTrackKind)
TRACK_KIND = {0: "unknown", 1: "file", 2: "cd", 3: "url", 4: "device", 5: "shared_library"}

_JA_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")


def guess_lang(*texts: str) -> str:
    """曲名/アーティスト/アルバム名から簡易的に言語を推定する ("ja" or "en")。

    かな・漢字が含まれていれば日本語曲とみなす。
    ※ 英語タイトルの邦楽(例: アーティスト名が英字)は誤判定しうる。PoCでの目安。
    """
    joined = " ".join(t for t in texts if t)
    return "ja" if _JA_RE.search(joined) else "en"


def connect_itunes():
    """iTunes COM に接続する (起動していなければ起動される)。"""
    import win32com.client as w

    return w.gencache.EnsureDispatch("iTunes.Application")


def to_file_track(track):
    """IITTrack を IITFileOrCDTrack にキャストする (Lyrics/Location 取得に必要)。"""
    import win32com.client as w

    try:
        return w.CastTo(track, "IITFileOrCDTrack")
    except Exception:
        return None


def persistent_id(itunes, track) -> str:
    """トラックの永続ID(16桁HEX)を返す。"""
    high = itunes.ITObjectPersistentIDHigh(track)
    low = itunes.ITObjectPersistentIDLow(track)
    return f"{high & 0xFFFFFFFF:08X}{low & 0xFFFFFFFF:08X}"


def find_track_by_pid(itunes, pid: str):
    high = int(pid[:8], 16)
    low = int(pid[8:], 16)
    # COM は符号付き32bitを期待するため変換
    if high >= 0x80000000:
        high -= 0x100000000
    if low >= 0x80000000:
        low -= 0x100000000
    return itunes.LibraryPlaylist.Tracks.ItemByPersistentID(high, low)
