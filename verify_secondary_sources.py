"""
Task 13: セカンダリ外部ソース クライアントの動作検証スクリプト
"""
import sys
import time
from pathlib import Path

from src.secondary_sources import (
    fetch_genius_lyrics,
    fetch_jlyric_lyrics,
    fetch_lyrics_ovh,
    fetch_secondary_lyrics_multistage,
    _get_cache_path
)
from src.config import SECONDARY_CACHE_DIR

def run_verification():
    print("=" * 60)
    print("Task 13: セカンダリ外部ソース クライアント 動作検証")
    print("=" * 60)

    # 1. Genius (洋楽ボーカル曲)
    print("\n--- [1] Genius: Asia - Heat of the Moment ---")
    t0 = time.time()
    lyrics, is_inst, err = fetch_genius_lyrics("Asia", "Heat of the Moment", use_cache=False)
    elapsed = time.time() - t0
    print(f"Result: lyrics_len={len(lyrics) if lyrics else 0}, is_inst={is_inst}, err={err}, elapsed={elapsed:.2f}s")
    assert lyrics is not None, "Genius should return lyrics for Asia - Heat of the Moment"
    assert not is_inst, "Asia - Heat of the Moment is not instrumental"
    print(f"Lyrics Preview:\n{lyrics[:120]}...\n")

    # 2. Genius (洋楽インストゥルメンタル曲)
    print("\n--- [2] Genius (Instrumental): The Flower Kings - Babylon ---")
    t0 = time.time()
    lyrics, is_inst, err = fetch_genius_lyrics("The Flower Kings", "Babylon", use_cache=False)
    elapsed = time.time() - t0
    print(f"Result: lyrics_len={len(lyrics) if lyrics else 0}, is_inst={is_inst}, err={err}, elapsed={elapsed:.2f}s")
    assert is_inst, "The Flower Kings - Babylon must be detected as instrumental"
    assert lyrics is None, "Instrumental track should have no lyrics"
    print("Instrumental detection PASS!")

    # 3. J-Lyric.net (邦楽ボーカル曲)
    print("\n--- [3] J-Lyric.net: THE ALFEE - 星空のディスタンス ---")
    t0 = time.time()
    lyrics, is_inst, err = fetch_jlyric_lyrics("THE ALFEE", "星空のディスタンス", use_cache=False)
    elapsed = time.time() - t0
    print(f"Result: lyrics_len={len(lyrics) if lyrics else 0}, is_inst={is_inst}, err={err}, elapsed={elapsed:.2f}s")
    assert lyrics is not None, "J-Lyric should return lyrics for THE ALFEE - 星空のディスタンス"
    assert not is_inst, "星空のディスタンス is not instrumental"
    print(f"Lyrics Preview:\n{lyrics[:120]}...\n")

    # 4. Lyrics.ovh (洋楽API)
    print("\n--- [4] Lyrics.ovh: Coldplay - Yellow ---")
    t0 = time.time()
    lyrics, is_inst, err = fetch_lyrics_ovh("Coldplay", "Yellow", use_cache=False)
    elapsed = time.time() - t0
    print(f"Result: lyrics_len={len(lyrics) if lyrics else 0}, is_inst={is_inst}, err={err}, elapsed={elapsed:.2f}s")
    assert lyrics is not None, "Lyrics.ovh should return lyrics for Coldplay - Yellow"
    assert not is_inst, "Coldplay - Yellow is not instrumental"
    print(f"Lyrics Preview:\n{lyrics[:120]}...\n")

    # 5. 多段階統合パイプライン & キャッシュ検証
    print("\n--- [5] 多段階統合パイプライン (邦楽 & 洋楽 & キャッシュ) ---")
    # 邦楽テスト
    t0 = time.time()
    lyr_ja, inst_ja, src_ja, err_ja = fetch_secondary_lyrics_multistage("THE ALFEE", "星空のディスタンス", language="ja")
    el_ja = time.time() - t0
    print(f"Multistage JA (cached): src={src_ja}, is_inst={inst_ja}, len={len(lyr_ja) if lyr_ja else 0}, elapsed={el_ja:.4f}s")
    assert src_ja == "jlyric"
    assert lyr_ja is not None
    assert el_ja < 0.1, "Cache hit should be instantaneous"

    # 洋楽ボーカルテスト
    t0 = time.time()
    lyr_en, inst_en, src_en, err_en = fetch_secondary_lyrics_multistage("Asia", "Heat of the Moment", language="en")
    el_en = time.time() - t0
    print(f"Multistage EN (cached): src={src_en}, is_inst={inst_en}, len={len(lyr_en) if lyr_en else 0}, elapsed={el_en:.4f}s")
    assert src_en == "genius"
    assert lyr_en is not None
    assert el_en < 0.1, "Cache hit should be instantaneous"

    # 洋楽インストテスト
    t0 = time.time()
    lyr_inst, inst_val, src_inst, err_inst = fetch_secondary_lyrics_multistage("The Flower Kings", "Babylon", language="en")
    el_inst = time.time() - t0
    print(f"Multistage EN Inst (cached): src={src_inst}, is_inst={inst_val}, elapsed={el_inst:.4f}s")
    assert src_inst == "genius"
    assert inst_val is True
    assert el_inst < 0.1, "Cache hit should be instantaneous"

    # キャッシュファイルの存在確認
    cache_files = list(SECONDARY_CACHE_DIR.glob("*.json"))
    print(f"\nCache Directory Files count: {len(cache_files)}")
    for cf in cache_files[:5]:
        print(f"  - {cf.name}")

    print("\n" + "=" * 60)
    print("ALL VERIFICATION CHECKS PASSED SUCCESSFULLY (100%)")
    print("=" * 60)

if __name__ == "__main__":
    run_verification()
