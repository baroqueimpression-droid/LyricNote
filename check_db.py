import sys
sys.path.append(".")
from src.db import get_connection

conn = get_connection()
total_tracks = conn.execute("SELECT count(*) as c FROM tracks").fetchone()["c"]
status_counts = dict(conn.execute("SELECT status, count(*) FROM tracks GROUP BY status").fetchall())

total_albums = conn.execute("SELECT count(DISTINCT artist || ':' || album) as c FROM tracks").fetchone()["c"]

# アルバム単位の進捗: 未処理トラックが0曲のアルバム＝処理完了アルバム
alb_rows = conn.execute("""
    SELECT artist, album,
           SUM(CASE WHEN status = 'unprocessed' THEN 1 ELSE 0 END) as unproc,
           count(*) as total
    FROM tracks
    GROUP BY artist, album
""").fetchall()

completed_albums = sum(1 for a in alb_rows if a["unproc"] == 0)
in_progress_albums = sum(1 for a in alb_rows if 0 < a["unproc"] < a["total"])
unstarted_albums = sum(1 for a in alb_rows if a["unproc"] == a["total"])

conn.close()

unprocessed = status_counts.get("unprocessed", 0)
processed_tracks = total_tracks - unprocessed
track_percent = (processed_tracks / total_tracks * 100) if total_tracks else 0
album_percent = (completed_albums / total_albums * 100) if total_albums else 0

ready = status_counts.get("ready_to_write", 0)
completed = status_counts.get("completed", 0)
lyrics_not_found = status_counts.get("lyrics_not_found", 0)
trans_failed = status_counts.get("translation_failed", 0)

print("==================================================")
print("             LyricNote 進捗状況サマリー            ")
print("==================================================")
print(f"【楽曲（トラック）進捗】: {processed_tracks} / {total_tracks} 曲 ({track_percent:.1f}%)")
print(f"  ・書き込み待機完了 (ready_to_write): {ready} 曲")
print(f"  ・iTunes書込済 (completed)         : {completed} 曲")
print(f"  ・歌詞未検出 (lyrics_not_found)    : {lyrics_not_found} 曲")
print(f"  ・和訳失敗 (translation_failed)    : {trans_failed} 曲")
print(f"  ・未処理 (unprocessed)             : {unprocessed} 曲")
print("")
print(f"【アルバム進捗】: {completed_albums} / {total_albums} 枚 ({album_percent:.1f}%)")
print(f"  ・全曲処理完了アルバム: {completed_albums} 枚")
print(f"  ・処理中アルバム      : {in_progress_albums} 枚")
print(f"  ・未着手アルバム      : {unstarted_albums} 枚")
print("==================================================")

