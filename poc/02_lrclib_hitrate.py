"""試作② LRCLIB ヒット率計測。

使い方:
  python 02_lrclib_hitrate.py [--n 100] [--lang en] [--seed 42]

- tracks_scan.csv (試作①の scan 結果) からローカルファイルのある曲をランダムに抽出し、
  LRCLIB で歌詞が取得できるかを計測する。
- 取得した歌詞は DATA_DIR/lrclib_cache に保存する(私的利用・非共有)。画面には統計のみ表示。
- 照合: 1) /api/get (曲名+アーティスト+アルバム+再生時間 で厳密一致)
        2) /api/search (表記ゆれを除去した曲名+アーティスト) → 再生時間差 ±3秒以内を採用
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import re
import statistics
import time

import requests

from common import LRCLIB_CACHE_DIR, TRACKS_CSV

API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "Lylic-PoC/0.1 (personal lyrics translation test)"}
DURATION_TOL = 3  # 秒

# 曲名の表記ゆれ (リマスター表記・feat. 等) を除去
_PAREN_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*(remaster|remix|live|version|edit|mono|stereo|feat\.?|ft\.|bonus|demo|deluxe|mix)[^\)\]]*[\)\]]",
    re.I,
)
_DASH_NOISE = re.compile(r"\s+-\s+.*(remaster|live|version|edit|mono|stereo|mix).*$", re.I)


def clean_title(s: str) -> str:
    s = _PAREN_NOISE.sub("", s)
    s = _DASH_NOISE.sub("", s)
    return s.strip()


def clean_artist(s: str) -> str:
    return re.split(r"\s+(feat\.?|ft\.|featuring|&|,|with)\s+", s, maxsplit=1, flags=re.I)[0].strip()


def lrclib_get(r: dict):
    params = {
        "track_name": r["name"],
        "artist_name": r["artist"],
        "album_name": r["album"],
        "duration": round(float(r["duration"] or 0)),
    }
    res = requests.get(f"{API}/get", params=params, headers=HEADERS, timeout=20)
    if res.status_code == 404:
        return None
    res.raise_for_status()
    return res.json()


def lrclib_search(r: dict):
    params = {"track_name": clean_title(r["name"]), "artist_name": clean_artist(r["artist"])}
    res = requests.get(f"{API}/search", params=params, headers=HEADERS, timeout=20)
    res.raise_for_status()
    dur = float(r["duration"] or 0)
    cands = [c for c in res.json() if abs((c.get("duration") or 0) - dur) <= DURATION_TOL]
    # 歌詞本文があるものを優先、次に再生時間が近いもの
    cands.sort(key=lambda c: (not c.get("plainLyrics"), abs((c.get("duration") or 0) - dur)))
    return cands[0] if cands else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--lang", default="en", choices=["en", "ja", "all"])
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    with open(TRACKS_CSV, encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["file_exists"] == "True"]
    if args.lang != "all":
        rows = [r for r in rows if r["lang"] == args.lang]
    random.Random(args.seed).shuffle(rows)
    sample = rows[: args.n]
    print(f"対象: {len(sample)}曲 (lang={args.lang}, 母数 {len(rows)})")

    LRCLIB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stats = collections.Counter()
    diffs, times, misses = [], [], []

    for i, r in enumerate(sample, 1):
        cache = LRCLIB_CACHE_DIR / f"{r['pid']}.json"
        t0 = time.time()
        hit, how = None, "miss"
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            hit, how = data.get("result"), data.get("how", "miss")
        else:
            try:
                hit = lrclib_get(r)
                how = "get" if hit else "miss"
                if not hit or not (hit.get("plainLyrics") or hit.get("instrumental")):
                    time.sleep(0.2)
                    s = lrclib_search(r)
                    if s:
                        hit, how = s, "search"
            except Exception as e:
                how = f"error:{type(e).__name__}"
            cache.write_text(json.dumps({"pid": r["pid"], "how": how, "result": hit}, ensure_ascii=False), encoding="utf-8")
            time.sleep(0.2)  # サーバー負荷への配慮
        times.append(time.time() - t0)

        if hit and hit.get("instrumental"):
            stats["instrumental"] += 1
        elif hit and hit.get("plainLyrics"):
            stats[f"hit_{how}"] += 1
            if hit.get("syncedLyrics"):
                stats["has_synced"] += 1
            diffs.append(abs((hit.get("duration") or 0) - float(r["duration"] or 0)))
        else:
            stats[how if how.startswith("error") else "miss"] += 1
            misses.append(f"{r['artist']} - {r['name']}")
        if i % 20 == 0:
            print(f"  ... {i}/{len(sample)}")

    n = len(sample)
    hits = stats["hit_get"] + stats["hit_search"]
    print("\n=== 結果 ===")
    print(f"歌詞取得成功: {hits}/{n} ({hits / n:.0%})  [厳密一致 {stats['hit_get']} / 検索一致 {stats['hit_search']}]")
    print(f"インスト判定: {stats['instrumental']}  未発見: {stats['miss']}  エラー: {sum(v for k, v in stats.items() if k.startswith('error'))}")
    print(f"うち同期歌詞(LRC)あり: {stats['has_synced']}")
    if diffs:
        print(f"再生時間差: 平均 {statistics.mean(diffs):.1f}s / 最大 {max(diffs):.1f}s")
    print(f"1曲あたり平均 {statistics.mean(times):.2f}s (キャッシュ含む)")
    if misses:
        print("\n未発見の例 (最大20件):")
        for m in misses[:20]:
            print("  -", m)


if __name__ == "__main__":
    main()
