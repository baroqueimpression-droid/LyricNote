import subprocess
import sys

def run_all():
    tests = [
        "tests.test_phase1",
        "tests.test_full_pipeline",
        "tests.test_full_ui_flow",
        "tests.test_itunes_real",
        "tests.test_server_live",
        "tests.test_verification",
        "tests.test_batch_runner"
    ]

    print("==========================================================")
    print("      Lylic 全自動・網羅的リグレッション総合テスト")
    print("==========================================================")

    for t in tests:
        print(f"\n>> 実行中: {t} ...")
        res = subprocess.run([sys.executable, "-m", t], capture_output=True, text=True, encoding="cp932", errors="replace")
        if res.returncode == 0:
            print(f"   [PASS] {t}")
        else:
            print(f"   [FAIL] {t}")
            print("STDOUT:", res.stdout)
            print("STDERR:", res.stderr)
            sys.exit(1)

    print("\n==========================================================")
    print("  全5種類の包括テストが全て合格しました！不具合ゼロを確認。")
    print("==========================================================")

if __name__ == "__main__":
    run_all()
