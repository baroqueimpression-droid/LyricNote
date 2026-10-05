import json
import time
import requests
from typing import List, Generator, Tuple
from src.config import OLLAMA_BASE_URL, RECOMMENDED_MODEL

def list_installed_models(max_retries: int = 3) -> List[str]:
    """
    インストール済みのOllamaモデル名一覧を取得する。
    requestsによるリトライと確実なセッション終了でWindowsソケットエラーを防止。
    """
    url = f"{OLLAMA_BASE_URL}/api/tags"

    for attempt in range(max_retries):
        try:
            with requests.Session() as s:
                res = s.get(url, timeout=3, headers={"Connection": "close"})
                if res.status_code == 200:
                    data = res.json()
                    return [m["name"] for m in data.get("models", [])]
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue
            return []
    return []

def is_model_installed(model_name: str = RECOMMENDED_MODEL) -> bool:
    """指定モデルがインストールされているか確認する"""
    models = list_installed_models()
    for m in models:
        if m == model_name or m.startswith(f"{model_name}:"):
            return True
    return False

def pull_model_stream(model_name: str = RECOMMENDED_MODEL) -> Generator[Tuple[str, float], None, None]:
    """
    モデルをストリーミングでダウンロード（Pull）し、(状態文字列, 進捗率 0.0-1.0) をyieldする。
    """
    url = f"{OLLAMA_BASE_URL}/api/pull"
    payload = {"name": model_name, "stream": True}

    try:
        with requests.Session() as s:
            with s.post(url, json=payload, stream=True, timeout=3600, headers={"Connection": "close"}) as res:
                for line in res.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line.decode("utf-8"))
                    status = data.get("status", "")
                    completed = data.get("completed", 0)
                    total = data.get("total", 0)

                    progress = 0.0
                    if total > 0:
                        progress = completed / total
                    yield status, progress
    except Exception as e:
        yield f"ダウンロードエラー: {e}", -1.0
