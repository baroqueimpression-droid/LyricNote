import win32com.client
from src.poc_helpers import persistent_id, find_track_by_pid, to_file_track
from src.db import init_db

def test_itunes_quick():
    print("=== iTunes COM 実機読み取りテスト ===")
    init_db()

    try:
        itunes = win32com.client.Dispatch("iTunes.Application")
    except Exception as e:
        print(f"iTunes未起動または未インストール: {e}")
        return

    library = itunes.LibraryPlaylist
    tracks = library.Tracks
    total = tracks.Count
    print(f"iTunes ライブラリ総トラック数: {total}")

    # 先頭3曲を安全に読み取るテスト
    tested_pids = []
    for i in range(1, min(4, total + 1)):
        t = tracks.Item(i)
        name = getattr(t, "Name", "")
        artist = getattr(t, "Artist", "")
        pid = persistent_id(itunes, t)
        tested_pids.append((pid, name, artist))
        print(f"[{i}] PID: {pid} | {artist} - {name}")

    print("\n--- PIDによる高速ダイレクト取得テスト ---")
    for pid, name, artist in tested_pids:
        found = find_track_by_pid(itunes, pid)
        assert found is not None, f"PID {pid} が見つかりません"
        file_track = to_file_track(found)
        assert file_track is not None, f"キャストに失敗しました (PID: {pid})"
        print(f"[OK] 高速取得成功: {found.Name} (PID: {pid})")

    print("\n=== iTunes COM 実機検証: 完全合格 ===")

if __name__ == "__main__":
    test_itunes_quick()
