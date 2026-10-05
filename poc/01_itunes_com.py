"""試作① iTunes COM 接続テスト。

使い方 (venv の python で実行):
  python 01_itunes_com.py scan                  # 全曲スキャン → tracks_scan.csv (歌詞本文は保存しない)
  python 01_itunes_com.py write-test [--pid PID] [--keep]
        # 1曲に「ダミー歌詞」を書き込み → 読み戻し検証 → 元の歌詞に復元
        # --keep を付けると復元せず残す (iTunes 画面/iPhone 表示確認用)
  python 01_itunes_com.py restore --pid PID     # バックアップから元の歌詞に戻す
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import time
from datetime import datetime

from common import (
    BACKUP_DIR,
    TRACK_KIND,
    TRACKS_CSV,
    connect_itunes,
    find_track_by_pid,
    guess_lang,
    persistent_id,
    to_file_track,
)

FIELDS = [
    "pid", "name", "artist", "album_artist", "album", "year", "genre",
    "duration", "kind", "ext", "file_exists", "has_lyrics", "lyrics_len", "lang",
]

# 実際の歌詞は使わず、レイアウト確認用のダミー文 (英詞 → 和訳詞 → 解説)
DUMMY_LYRICS = "\r".join([
    "[LYLIC TEST] English lyrics block (dummy)",
    "This is line one of a dummy verse",
    "This is line two of a dummy verse",
    "",
    "This is a dummy chorus line",
    "",
    "――――――――――――",
    "【和訳】",
    "これはダミーのヴァース1行目",
    "これはダミーのヴァース2行目",
    "",
    "これはダミーのコーラス行",
    "",
    "――――――――――――",
    "【解説】",
    "書き込みテスト用のダミー文です。改行・全角記号・絵文字なし。",
])


def cmd_scan(_args):
    it = connect_itunes()
    tracks = it.LibraryPlaylist.Tracks
    total = tracks.Count
    print(f"iTunes {it.Version} / ライブラリ曲数: {total}")
    rows = []
    t0 = time.time()
    for i in range(1, total + 1):
        tr = tracks.Item(i)
        row = dict.fromkeys(FIELDS, "")
        try:
            row["pid"] = persistent_id(it, tr)
            row["name"] = tr.Name or ""
            row["artist"] = tr.Artist or ""
            row["album"] = tr.Album or ""
            row["year"] = tr.Year or ""
            row["genre"] = tr.Genre or ""
            row["duration"] = tr.Duration or 0
            row["kind"] = TRACK_KIND.get(tr.Kind, str(tr.Kind))
            ft = to_file_track(tr) if tr.Kind == 1 else None
            if ft is not None:
                try:
                    row["album_artist"] = ft.AlbumArtist or ""
                except Exception:
                    pass
                loc = ""
                try:
                    loc = ft.Location or ""
                except Exception:
                    pass
                row["ext"] = os.path.splitext(loc)[1].lower() if loc else ""
                row["file_exists"] = bool(loc)
                # Lyrics 取得は iTunes が実ファイルを読むため遅い (1曲1.5〜2.5秒)。既定では省略。
                if _args.with_lyrics:
                    try:
                        lyr = ft.Lyrics or ""
                    except Exception:
                        lyr = ""
                    row["has_lyrics"] = bool(lyr.strip())
                    row["lyrics_len"] = len(lyr)
            row["lang"] = guess_lang(row["name"], row["artist"], row["album"])
        except Exception as e:  # 1曲の失敗で全体を止めない
            row["name"] = row["name"] or f"<error: {e}>"
        rows.append(row)
        if i % 200 == 0:
            el = time.time() - t0
            print(f"  ... {i}/{total} ({el:.0f}s, {i / el:.1f}曲/s, 残り約{(total - i) / (i / el) / 60:.1f}分)", flush=True)

    with open(TRACKS_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"\nスキャン完了 {time.time() - t0:.0f}s → {TRACKS_CSV}")
    c = collections.Counter
    print("種別:", dict(c(r["kind"] for r in rows)))
    print("拡張子:", dict(c(r["ext"] or "(なし/クラウド)" for r in rows)))
    local = [r for r in rows if r["file_exists"] is True]
    print(f"ローカルファイルあり: {len(local)} / {total}")
    print("言語推定(全体):", dict(c(r["lang"] for r in rows)))
    print("言語推定(ローカル):", dict(c(r["lang"] for r in local)))
    en_local = [r for r in local if r["lang"] == "en"]
    print(f"英語曲(ローカル): {len(en_local)}  日本語曲(ローカル): {len(local) - len(en_local)}")
    if _args.with_lyrics:
        print(f"既存歌詞あり(ローカル): {sum(1 for r in local if r['has_lyrics'])}")
        print(f"英語曲で既存歌詞あり: {sum(1 for r in en_local if r['has_lyrics'])}")


def _backup_path(pid: str):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR / f"{pid}.json"


def _pick_auto(it):
    """英語曲・ローカルファイルありの曲を1つ選ぶ (scan結果を優先)。"""
    if TRACKS_CSV.exists():
        with open(TRACKS_CSV, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r["file_exists"] == "True" and r["lang"] == "en" and r["ext"] in (".m4a", ".mp3"):
                    return r["pid"]
    raise SystemExit("先に scan を実行するか --pid を指定してください")


def cmd_write_test(args):
    it = connect_itunes()
    pid = args.pid or _pick_auto(it)
    tr = find_track_by_pid(it, pid)
    ft = to_file_track(tr)
    if ft is None:
        raise SystemExit("ファイルトラックにキャストできません")
    print(f"対象: [{pid}] {tr.Artist} - {tr.Name} ({ft.Location})")

    original = ft.Lyrics or ""
    bp = _backup_path(pid)
    if not bp.exists():  # 初回のみ保存(上書きで原本を失わないため)
        bp.write_text(json.dumps({
            "pid": pid, "name": tr.Name, "artist": tr.Artist,
            "saved_at": datetime.now().isoformat(), "lyrics": original,
        }, ensure_ascii=False), encoding="utf-8")
    print(f"元の歌詞: {len(original)}文字 → バックアップ {bp}")

    t0 = time.time()
    ft.Lyrics = DUMMY_LYRICS
    print(f"書き込み: {time.time() - t0:.2f}s")

    # 新しく取り直して検証
    ft2 = to_file_track(find_track_by_pid(it, pid))
    got = ft2.Lyrics or ""
    norm = lambda s: s.replace("\r\n", "\n").replace("\r", "\n")
    ok = norm(got) == norm(DUMMY_LYRICS)
    print(f"読み戻し一致: {ok} (書込 {len(DUMMY_LYRICS)}文字 / 読戻 {len(got)}文字)")
    if not ok:
        print("  改行コード差分などを確認してください:", repr(got[:80]))

    if args.keep:
        print("--keep 指定: ダミー歌詞を残しました。iTunes で[曲の情報]→[歌詞]を確認してください。")
        print(f"戻すとき: python 01_itunes_com.py restore --pid {pid}")
    else:
        ft2.Lyrics = original
        back = to_file_track(find_track_by_pid(it, pid)).Lyrics or ""
        print(f"復元: {'OK' if norm(back) == norm(original) else 'NG'}")


def cmd_restore(args):
    it = connect_itunes()
    bp = _backup_path(args.pid)
    data = json.loads(bp.read_text(encoding="utf-8"))
    ft = to_file_track(find_track_by_pid(it, args.pid))
    ft.Lyrics = data["lyrics"]
    print(f"復元しました: {data['artist']} - {data['name']} ({len(data['lyrics'])}文字)")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--with-lyrics", action="store_true", help="既存歌詞の有無も取得(低速)")
    w = sub.add_parser("write-test")
    w.add_argument("--pid")
    w.add_argument("--keep", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("--pid", required=True)
    args = p.parse_args()
    {"scan": cmd_scan, "write-test": cmd_write_test, "restore": cmd_restore}[args.cmd](args)


if __name__ == "__main__":
    main()
