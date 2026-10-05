import hashlib
import re
import json
import requests
from pathlib import Path
from typing import Optional, Tuple, List
from src.config import CACHE_DIR, assert_safe_path

def clean_track_title(title: str) -> str:
    """曲名からカッコ内の補足表記（Remaster, Live, Version等）を除去して検索用タイトルを生成する"""
    # カッコ補足語の除去
    patterns = [
        r"\s*[\(\[\{（［【].*?(?:Remaster|Live|Version|Track|Mix|Recording|Audio|Original|Edit|Take|Mono|Stereo|Deluxe|Bonus).*?[\)\]\}）］】]",
        r"\s*-\s*(?:Remaster|Live|Version|Mix|Original).*$",
        r"^\s*\d+[\.\s\-]+\s*" # 先頭のトラック番号
    ]
    cleaned = title
    for p in patterns:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or title

def get_artist_variations(artist: str) -> List[str]:
    """アーティスト名の表記揺れバリエーション（THEの有無など）を生成する"""
    variations = [artist.strip()]
    upper_a = artist.strip().upper()
    if upper_a.startswith("THE "):
        variations.append(artist.strip()[4:].strip())
    else:
        variations.append(f"THE {artist.strip()}")
    return list(dict.fromkeys([v for v in variations if v]))

def fetch_lyrics_multistage(
    artist: str, 
    title: str, 
    album: str, 
    duration: int = 0,
    existing_lyrics: Optional[str] = None
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    多段階フォールバック検索による堅牢な歌詞取得 (v2.1仕様)
    戻り値: (lyrics, source, error_code)
      source: 'itunes' | 'lrclib_get' | 'lrclib_search' | None
      error_code: None | 'not_in_lrclib' | 'network_error'
    """
    # 1. 第1優先: iTunes内にすでに歌詞が存在する場合は即座に再利用
    if existing_lyrics and existing_lyrics.strip():
        return existing_lyrics.strip(), "itunes", None

    assert_safe_path(CACHE_DIR)
    raw_key = f"{artist}_{album}_{title}"
    safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', raw_key)[:80]
    hash_suffix = hashlib.md5(raw_key.encode("utf-8", errors="replace")).hexdigest()[:8]
    cache_file = CACHE_DIR / f"{safe_name}_{hash_suffix}.json"

    # キャッシュチェック

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("notFound"):
                    pass # 再試行のため一旦スルーまたは即時返却
                else:
                    lyrics = data.get("plainLyrics") or data.get("syncedLyrics")
                    if lyrics:
                        return lyrics, "cache", None
        except Exception:
            pass

    headers = {"User-Agent": "LylicNoteApp/2.1", "Connection": "close"}

    # 2. 第2優先: LRCLIB /api/get による厳密一致取得
    get_params = {
        "artist_name": artist,
        "track_name": title,
        "album_name": album,
    }
    if duration > 0:
        get_params["duration"] = str(duration)

    try:
        with requests.Session() as s:
            # 厳密取得トライ
            res = s.get("https://lrclib.net/api/get", params=get_params, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                lyrics = data.get("plainLyrics") or data.get("syncedLyrics")
                if lyrics:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                    return lyrics, "lrclib_get", None

            # 3. 第3優先: 正規化クエリによる /api/search あいまい検索
            cleaned_title = clean_track_title(title)
            artist_vars = get_artist_variations(artist)

            search_queries = [
                f"{artist} {cleaned_title}",
                f"{artist} {title}"
            ]
            for a_var in artist_vars:
                if a_var != artist:
                    search_queries.append(f"{a_var} {cleaned_title}")

            for query in dict.fromkeys(search_queries):
                s_res = s.get("https://lrclib.net/api/search", params={"q": query}, headers=headers, timeout=8)
                if s_res.status_code == 200:
                    results = s_res.json()
                    if isinstance(results, list) and results:
                        for item in results:
                            l = item.get("plainLyrics") or item.get("syncedLyrics")
                            if l:
                                with open(cache_file, "w", encoding="utf-8") as f:
                                    json.dump(item, f, ensure_ascii=False)
                                return l, "lrclib_search", None

            # どこにも見つからなかった場合
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"notFound": True}, f)
            return None, None, "not_in_lrclib"

    except Exception as e:
        print(f"LRCLIB通信エラー ({artist} - {title}): {e}")
        return None, None, f"network_error: {e}"

def fetch_lyrics_lrclib(artist: str, title: str, album: str, duration: int) -> Optional[str]:
    """後方互換用ラッパー"""
    lyrics, _, _ = fetch_lyrics_multistage(artist, title, album, duration)
    return lyrics
