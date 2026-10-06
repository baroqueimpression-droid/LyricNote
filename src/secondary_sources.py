"""
セカンダリ外部ソース クライアントモジュール (v2.2仕様)
- Genius (洋楽プログレ・オルタナティブ・ロック・インスト判定)
- J-Lyric.net (邦楽全般・J-POP・昭和歌謡・フォーク)
- Lyrics.ovh (パブリック軽量REST API)
- 多段階フォールバッククライアント
- キャッシュ機能 (X:\\LylicData\\secondary_cache)
- Politeness Policy (J-Lyric: 1.5秒以上のウェイト)
- エラーハンドリング・リトライ・指数バックオフ
"""

import os
import re
import time
import json
import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
import requests
from bs4 import BeautifulSoup

from src.config import SECONDARY_CACHE_DIR, assert_safe_path, ensure_data_directories

logger = logging.getLogger(__name__)

# Politeness Policy 定数
JLYRIC_POLITENESS_DELAY = 1.5  # 秒
_last_jlyric_request_time: float = 0.0

# 共通リクエストヘッダー
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
# Context7 推奨パターン: (connect_timeout, read_timeout) タプル指定
# TCP再送ウィンドウを考慮し connect は 3.05 秒に設定してフェイルファスト化
DEFAULT_TIMEOUT = (3.05, 10.0)
LYRICS_OVH_TIMEOUT = (3.05, 5.0)  # 外部無料API用フェイルファストタイムアウト


def _wait_jlyric_politeness() -> None:
    """J-Lyric.net に対する過度な負荷を防止するための待機ウェイト (最低1.5秒)"""
    global _last_jlyric_request_time
    now = time.time()
    elapsed = now - _last_jlyric_request_time
    if elapsed < JLYRIC_POLITENESS_DELAY:
        sleep_duration = JLYRIC_POLITENESS_DELAY - elapsed
        time.sleep(sleep_duration)
    _last_jlyric_request_time = time.time()


def _normalize_string(s: str) -> str:
    """比較用の正規化（英数字小文字化、記号・空白除去）"""
    if not s:
        return ""
    return re.sub(r"[\s\W_]+", "", s).lower()


def is_japanese_text(text: str) -> bool:
    """テキストにひらがな・カタカナ・漢字が含まれているかを判定する"""
    if not text:
        return False
    return bool(re.search(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", text))


# ==============================================================================
# キャッシュ管理 (X:\LylicData\secondary_cache)
# ==============================================================================

def _get_cache_path(source: str, artist: str, title: str) -> Path:
    """キャッシュファイルのパスを生成・検証する"""
    ensure_data_directories()
    assert_safe_path(SECONDARY_CACHE_DIR)
    
    key_src = f"{source}:{artist.strip().lower()}:{title.strip().lower()}"
    key_hash = hashlib.sha256(key_src.encode("utf-8", errors="replace")).hexdigest()[:16]
    safe_artist = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', artist.strip())[:30]
    safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', title.strip())[:30]
    file_name = f"{source}_{safe_artist}_{safe_title}_{key_hash}.json"
    cache_path = SECONDARY_CACHE_DIR / file_name
    return assert_safe_path(cache_path)


def _load_from_cache(source: str, artist: str, title: str) -> Optional[Tuple[Optional[str], bool, Optional[str]]]:
    """
    キャッシュが存在すれば (lyrics, is_instrumental, error) を返却する。
    キャッシュがない場合は None を返却。
    """
    try:
        cache_path = _get_cache_path(source, artist, title)
        if cache_path.exists():
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return (data.get("lyrics"), bool(data.get("is_instrumental", False)), data.get("error"))
    except Exception as e:
        logger.warning(f"Failed to read cache for {source} - {artist} - {title}: {e}")
    return None


def _save_to_cache(
    source: str, 
    artist: str, 
    title: str, 
    lyrics: Optional[str], 
    is_instrumental: bool, 
    error: Optional[str]
) -> None:
    """取得結果（または確定エラー）をローカルキャッシュに保存する"""
    try:
        cache_path = _get_cache_path(source, artist, title)
        payload = {
            "source": source,
            "artist": artist,
            "title": title,
            "lyrics": lyrics,
            "is_instrumental": is_instrumental,
            "error": error,
            "cached_at": time.time()
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to write cache for {source} - {artist} - {title}: {e}")


# ==============================================================================
# セカンダリ1: Genius API & スクレイピング
# ==============================================================================

def fetch_genius_lyrics(
    artist: str, 
    title: str, 
    use_cache: bool = True
) -> Tuple[Optional[str], bool, Optional[str]]:
    """
    Genius から歌詞を取得、またはインストゥルメンタル曲かを判定する。
    戻り値: (lyrics, is_instrumental, error)
    """
    if use_cache:
        cached = _load_from_cache("genius", artist, title)
        if cached is not None:
            return cached

    access_token = os.environ.get("GENIUS_ACCESS_TOKEN", "").strip()
    headers = dict(DEFAULT_HEADERS)
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
        search_url = "https://api.genius.com/search"
    else:
        search_url = "https://genius.com/api/search/multi"

    query = f"{artist} {title}".strip()
    song_url = None

    # 1. 検索リクエスト (最大2回リトライ)
    for attempt in range(2):
        try:
            res = requests.get(search_url, params={"q": query}, headers=headers, timeout=DEFAULT_TIMEOUT)
            if res.status_code == 200:
                data = res.json()
                sections = data.get("response", {}).get("sections", [])
                hits: List[Dict[str, Any]] = []
                for sec in sections:
                    if sec.get("type") == "song":
                        hits = sec.get("hits", [])
                        break
                
                # api.genius.com の直接 hits フォーマット対応
                if not hits and "hits" in data.get("response", {}):
                    hits = data.get("response", {}).get("hits", [])

                if not hits:
                    _save_to_cache("genius", artist, title, None, False, "not_found")
                    return (None, False, "not_found")

                # タイトルまたはアーティストの合致度を確認
                norm_art = _normalize_string(artist)
                norm_title = _normalize_string(title)
                best_hit = None

                for h in hits:
                    r_item = h.get("result", {})
                    h_title = _normalize_string(r_item.get("title", ""))
                    h_art = _normalize_string(r_item.get("primary_artist", {}).get("name", ""))
                    
                    if norm_title in h_title or h_title in norm_title:
                        if norm_art in h_art or h_art in norm_art or not norm_art:
                            best_hit = r_item
                            break
                
                # 最も近い曲または第1候補を採用
                if not best_hit and hits:
                    best_hit = hits[0].get("result", {})

                if best_hit:
                    song_url = best_hit.get("url")
                break
            elif res.status_code in (404, 400):
                _save_to_cache("genius", artist, title, None, False, f"http_{res.status_code}")
                return (None, False, f"http_{res.status_code}")
            else:
                time.sleep(1.0 * (attempt + 1))
        except Exception as e:
            if attempt == 1:
                return (None, False, f"network_error: {e}")
            time.sleep(1.0)

    if not song_url:
        _save_to_cache("genius", artist, title, None, False, "not_found")
        return (None, False, "not_found")

    # 2. 楽曲ページの取得とパース
    for attempt in range(2):
        try:
            page_res = requests.get(song_url, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
            if page_res.status_code != 200:
                if attempt == 1:
                    _save_to_cache("genius", artist, title, None, False, f"page_http_{page_res.status_code}")
                    return (None, False, f"page_http_{page_res.status_code}")
                time.sleep(1.0)
                continue

            soup = BeautifulSoup(page_res.text, "html.parser")

            # インスト判定の確認 (メッセージ要素やテキスト)
            inst_msg = soup.find(
                lambda tag: tag.name in ["div", "span", "p"] and "this song is an instrumental" in tag.get_text().lower()
            )
            if inst_msg:
                _save_to_cache("genius", artist, title, None, True, None)
                return (None, True, None)

            # 不要ヘッダーの除去
            for h in soup.find_all(class_=lambda c: c and "LyricsHeader" in c):
                h.decompose()

            # 歌詞コンテナの抽出
            containers = soup.find_all("div", attrs={"data-lyrics-container": "true"})
            if not containers:
                # コンテナがないがページ内に instrumental 表記がある場合
                page_text_lower = soup.get_text().lower()
                if "instrumental" in page_text_lower:
                    _save_to_cache("genius", artist, title, None, True, None)
                    return (None, True, None)
                _save_to_cache("genius", artist, title, None, False, "no_lyrics_container")
                return (None, False, "no_lyrics_container")

            text_chunks = []
            for c in containers:
                for br in c.find_all("br"):
                    br.replace_with("\n")
                text_chunks.append(c.get_text())

            raw_lyrics = "\n".join(text_chunks).strip()

            # 歌詞内容によるインスト判定チェック
            cleaned_upper = raw_lyrics.strip().upper()
            if cleaned_upper in ("[INSTRUMENTAL]", "INSTRUMENTAL") or "THIS SONG IS AN INSTRUMENTAL" in cleaned_upper:
                _save_to_cache("genius", artist, title, None, True, None)
                return (None, True, None)

            if raw_lyrics:
                _save_to_cache("genius", artist, title, raw_lyrics, False, None)
                return (raw_lyrics, False, None)
            else:
                _save_to_cache("genius", artist, title, None, False, "empty_lyrics")
                return (None, False, "empty_lyrics")

        except Exception as e:
            if attempt == 1:
                return (None, False, f"parse_error: {e}")
            time.sleep(1.0)

    return (None, False, "unknown_error")


# ==============================================================================
# セカンダリ2: J-Lyric.net (邦楽専用)
# ==============================================================================

def fetch_jlyric_lyrics(
    artist: str, 
    title: str, 
    use_cache: bool = True
) -> Tuple[Optional[str], bool, Optional[str]]:
    """
    J-Lyric.net から邦楽歌詞を取得する。
    戻り値: (lyrics, is_instrumental, error)
    """
    if use_cache:
        cached = _load_from_cache("jlyric", artist, title)
        if cached is not None:
            return cached

    search_url = "http://j-lyric.net/search.php"
    params = {"kt": title, "ka": artist}
    detail_url = None

    # 1. 検索リクエスト (Politeness待機 + 最大2回リトライ)
    for attempt in range(2):
        try:
            _wait_jlyric_politeness()
            res = requests.get(search_url, params=params, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
            res.encoding = res.apparent_encoding or "utf-8"

            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                norm_art = _normalize_string(artist)
                norm_title = _normalize_string(title)

                # p.mid (曲名) と p.sml (アーティスト名) のペアを探索
                mid_tags = soup.find_all("p", class_="mid")
                candidate_url = None

                for mid in mid_tags:
                    title_a = mid.find("a")
                    if not title_a:
                        continue
                    c_title = title_a.text.strip()
                    c_href = title_a.get("href", "")

                    sml = mid.find_next_sibling("p", class_="sml")
                    artist_a = sml.find("a") if sml else None
                    c_artist = artist_a.text.strip() if artist_a else ""

                    c_norm_t = _normalize_string(c_title)
                    c_norm_a = _normalize_string(c_artist)

                    # 完全一致優先
                    if norm_title == c_norm_t and (norm_art in c_norm_a or c_norm_a in norm_art or not norm_art):
                        candidate_url = c_href
                        break
                    # 部分一致
                    if (norm_title in c_norm_t or c_norm_t in norm_title) and (norm_art in c_norm_a or c_norm_a in norm_art or not norm_art):
                        if not candidate_url:
                            candidate_url = c_href

                if candidate_url:
                    if candidate_url.startswith("http"):
                        detail_url = candidate_url
                    else:
                        detail_url = f"http://j-lyric.net{candidate_url}"
                break
            elif res.status_code in (404, 400):
                _save_to_cache("jlyric", artist, title, None, False, f"http_{res.status_code}")
                return (None, False, f"http_{res.status_code}")
            else:
                time.sleep(1.5)
        except Exception as e:
            if attempt == 1:
                return (None, False, f"network_error: {e}")
            time.sleep(1.5)

    if not detail_url:
        _save_to_cache("jlyric", artist, title, None, False, "not_found")
        return (None, False, "not_found")

    # 2. 詳細ページから歌詞本文の取得
    for attempt in range(2):
        try:
            _wait_jlyric_politeness()
            detail_res = requests.get(detail_url, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
            detail_res.encoding = detail_res.apparent_encoding or "utf-8"

            if detail_res.status_code == 200:
                soup = BeautifulSoup(detail_res.text, "html.parser")
                lyric_p = soup.find("p", id="Lyric")
                if lyric_p:
                    for br in lyric_p.find_all("br"):
                        br.replace_with("\n")
                    lyrics = lyric_p.get_text().strip()
                    if lyrics:
                        _save_to_cache("jlyric", artist, title, lyrics, False, None)
                        return (lyrics, False, None)
                
                _save_to_cache("jlyric", artist, title, None, False, "empty_lyrics")
                return (None, False, "empty_lyrics")
            else:
                if attempt == 1:
                    _save_to_cache("jlyric", artist, title, None, False, f"detail_http_{detail_res.status_code}")
                    return (None, False, f"detail_http_{detail_res.status_code}")
                time.sleep(1.5)
        except Exception as e:
            if attempt == 1:
                return (None, False, f"detail_error: {e}")
            time.sleep(1.5)

    return (None, False, "unknown_error")


# ==============================================================================
# セカンダリ3: Lyrics.ovh (パブリック軽量REST API)
# ==============================================================================

def fetch_lyrics_ovh(
    artist: str, 
    title: str, 
    use_cache: bool = True
) -> Tuple[Optional[str], bool, Optional[str]]:
    """
    Lyrics.ovh から歌詞を取得する。
    戻り値: (lyrics, is_instrumental, error)
    """
    if use_cache:
        cached = _load_from_cache("lyrics_ovh", artist, title)
        if cached is not None:
            return cached

    url = f"https://api.lyrics.ovh/v1/{artist.strip()}/{title.strip()}"

    try:
        res = requests.get(url, headers=DEFAULT_HEADERS, timeout=LYRICS_OVH_TIMEOUT)
        if res.status_code == 200:
            data = res.json()
            raw_lyrics = data.get("lyrics", "").strip()
            if raw_lyrics:
                # ヘッダー表記（Paroles de la chanson ... など）のクリーンアップ
                cleaned = re.sub(r"^Paroles de la chanson[^\n]*\n+", "", raw_lyrics, flags=re.IGNORECASE)
                cleaned = cleaned.strip()
                _save_to_cache("lyrics_ovh", artist, title, cleaned, False, None)
                return (cleaned, False, None)
            else:
                _save_to_cache("lyrics_ovh", artist, title, None, False, "empty_lyrics")
                return (None, False, "empty_lyrics")
        elif res.status_code == 404:
            _save_to_cache("lyrics_ovh", artist, title, None, False, "not_found")
            return (None, False, "not_found")
        else:
            return (None, False, f"http_{res.status_code}")
    except requests.exceptions.ConnectTimeout:
        logger.warning(f"Lyrics.ovh connect timed out for {artist} - {title} (fail-fast)")
        return (None, False, "connect_timeout")
    except requests.exceptions.ReadTimeout:
        logger.warning(f"Lyrics.ovh read timed out for {artist} - {title}")
        return (None, False, "read_timeout")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Lyrics.ovh request failed for {artist} - {title}: {type(e).__name__}")
        return (None, False, f"network_error: {type(e).__name__}")
    except Exception as e:
        return (None, False, f"error: {e}")

    return (None, False, "unknown_error")


# ==============================================================================
# 多段階統合フォールバッククライアント
# ==============================================================================

def fetch_secondary_lyrics_multistage(
    artist: str, 
    title: str, 
    language: str = "unknown",
    use_cache: bool = True
) -> Tuple[Optional[str], bool, Optional[str], Optional[str]]:
    """
    洋楽・邦楽に応じた多段階フォールバック検索を実行する (v2.2仕様)。
    
    パイプライン優先順位:
      邦楽 (ja): [第1] J-Lyric.net -> [第2] Genius
      洋楽 (en/他): [第1] Genius -> [第2] Lyrics.ovh

    戻り値: (lyrics, is_instrumental, source, error)
      source: 'jlyric' | 'genius' | 'lyrics_ovh' | None
    """
    is_ja = (language == "ja") or is_japanese_text(artist) or is_japanese_text(title)

    if is_ja:
        # --- 邦楽パイプライン ---
        # 1. J-Lyric.net
        lyrics, is_inst, err = fetch_jlyric_lyrics(artist, title, use_cache=use_cache)
        if lyrics:
            return (lyrics, False, "jlyric", None)
        if is_inst:
            return (None, True, "jlyric", None)

        # 2. Genius (邦楽でも一部カバーあり、インスト判定も有効)
        lyrics, is_inst, err = fetch_genius_lyrics(artist, title, use_cache=use_cache)
        if lyrics:
            return (lyrics, False, "genius", None)
        if is_inst:
            return (None, True, "genius", None)

        return (None, False, None, err or "not_found_in_secondary")

    else:
        # --- 洋楽パイプライン ---
        # 1. Genius
        lyrics, is_inst, err = fetch_genius_lyrics(artist, title, use_cache=use_cache)
        if lyrics:
            return (lyrics, False, "genius", None)
        if is_inst:
            return (None, True, "genius", None)

        # 2. Lyrics.ovh
        lyrics, is_inst, err = fetch_lyrics_ovh(artist, title, use_cache=use_cache)
        if lyrics:
            return (lyrics, False, "lyrics_ovh", None)
        if is_inst:
            return (None, True, "lyrics_ovh", None)

        return (None, False, None, err or "not_found_in_secondary")
