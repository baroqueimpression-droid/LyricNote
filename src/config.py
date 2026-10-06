import os
from pathlib import Path

# ==============================================================================
# 絶対遵守ルール: データ保存先の制限
# ==============================================================================
BASE_DATA_DIR = Path("X:/LylicData").resolve()

# サブディレクトリ
DB_PATH = BASE_DATA_DIR / "library.db"
BACKUP_DIR = BASE_DATA_DIR / "lyrics_backup"
CACHE_DIR = BASE_DATA_DIR / "lrclib_cache"
SECONDARY_CACHE_DIR = BASE_DATA_DIR / "secondary_cache"

def ensure_data_directories() -> None:
    """Xドライブの専用ディレクトリが存在することを確認・作成する"""
    BASE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SECONDARY_CACHE_DIR.mkdir(parents=True, exist_ok=True)

def assert_safe_path(target_path: Path | str) -> Path:
    """
    指定されたパスが X:\\LylicData 配下にあるかを厳格に検証する。
    配下でない場合は例外を送出し、他のフォルダへのアクセスや操作を遮断する。
    """
    resolved = Path(target_path).resolve()
    try:
        resolved.relative_to(BASE_DATA_DIR)
    except ValueError:
        raise PermissionError(f"[SAFETY VIOLATION] X:\\LylicData 外部へのアクセスは禁止されています: {resolved}")
    return resolved

# ==============================================================================
# LLM設定
# ==============================================================================
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
RECOMMENDED_MODEL = "gemma2:9b"
FALLBACK_MODEL = "ABEJA-Qwen2.5-7b-Japanese-v0.1-gguf:Q4_K_M"

# UI設定
DEFAULT_SERVER_PORT = 7860
