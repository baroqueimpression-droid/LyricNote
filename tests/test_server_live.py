import time
import requests
import subprocess
import sys

def test_live_server():
    print("=== WebUI サーバー起動＆疎通テスト開始 ===")
    
    # バックグラウンドで app.py を起動
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.app"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8"
    )

    try:
        url = "http://127.0.0.1:7860/"
        server_ready = False
        
        # 起動待機 (最大20秒)
        for i in range(20):
            time.sleep(1)
            if proc.poll() is not None:
                out, err = proc.communicate()
                print("サーバーが異常終了しました:")
                print("STDOUT:", out)
                print("STDERR:", err)
                raise RuntimeError("Server process terminated unexpectedly")

            try:
                with requests.Session() as s:
                    res = s.get(url, timeout=3)
                    if res.status_code == 200:
                        print(f"[OK] HTTP 200 応答確認 (試行 {i+1}秒目): {url}")
                        server_ready = True
                        break
            except Exception:
                continue

        if not server_ready:
            raise TimeoutError("サーバーの起動タイムアウト (20秒)")

        # 連続リクエストによるソケット耐性テスト (WinError 10054 / 10022 検証)
        print("--- 連続リクエスト＆ソケット切断耐性テスト ---")
        with requests.Session() as s:
            for req_idx in range(5):
                res = s.get(url, timeout=3)
                assert res.status_code == 200
                time.sleep(0.2)
        print("[OK] 5回連続HTTPリクエスト: 正常終了（ソケットエラーなし）")

        print("=== 全サーバーテスト合格 ===")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        print("テスト用サーバープロセスを安全に停止しました。")

if __name__ == "__main__":
    test_live_server()
