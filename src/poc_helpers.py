import re
import win32com.client as w

_JA_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")

def is_likely_western(artist: str, name: str) -> bool:
    """曲名またはアーティスト名から、洋楽（非日本語）かどうかを判定"""
    text = f"{artist} {name}"
    return not bool(_JA_RE.search(text))

def to_file_track(track):
    """IITTrack を IITFileOrCDTrack にキャストする (Lyrics 取得・設定に必須)"""
    try:
        return w.CastTo(track, "IITFileOrCDTrack")
    except Exception:
        return None

def persistent_id(itunes, track) -> str:
    """トラックの永続ID(16桁HEX)を返す"""
    high = itunes.ITObjectPersistentIDHigh(track)
    low = itunes.ITObjectPersistentIDLow(track)
    return f"{high & 0xFFFFFFFF:08X}{low & 0xFFFFFFFF:08X}"

def find_track_by_pid(itunes, pid: str):
    """PID(16桁HEX)からトラックを高速に直接取得する"""
    try:
        high = int(pid[:8], 16)
        low = int(pid[8:], 16)
        if high >= 0x80000000:
            high -= 0x100000000
        if low >= 0x80000000:
            low -= 0x100000000
        return itunes.LibraryPlaylist.Tracks.ItemByPersistentID(high, low)
    except Exception:
        return None
