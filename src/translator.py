import json
import time
import requests
from typing import Optional, Dict, Any, Tuple
from src.config import OLLAMA_BASE_URL, RECOMMENDED_MODEL, FALLBACK_MODEL
from src.db import get_cached_translation, save_translation_memory
from src.poc_helpers import is_likely_western
from src.ollama_manager import list_installed_models

def select_best_available_model() -> str:
    """利用可能な最善のモデルを選択する"""
    installed = list_installed_models()
    for m in installed:
        if m.startswith("gemma2:9b") or m.startswith("gemma2"):
            return m
    for m in installed:
        if "abeja" in m.lower() or "qwen" in m.lower():
            return m
    return installed[0] if installed else RECOMMENDED_MODEL

def build_translation_system_prompt(artist: str, album: str, context_dict: Optional[Dict[str, Any]] = None) -> str:
    """
    クラウドLLMから引き継いだ「世界観・物語設定・用語集」を動的に埋め込んだシステムプロンプトを構築する。
    """
    prompt = f"""あなたは洋楽（特にプログレッシブ・ロックやロックオペラ、コンセプトアルバム）の歌詞翻訳における最高峰の翻訳家です。
アーティスト「{artist}」、アルバム「{album}」の世界観を完全に理解した上で、以下の指示に従い英語歌詞を格調高い日本語に和訳してください。

【翻訳方針】
1. 単語ごとの直訳（英語の授業のような訳）ではなく、行ごとの詩的な響き、リズム、情景が浮かぶ美しい日本語にすること。
2. 原文の各行に対応するよう、行数を揃えて翻訳すること。
3. 英語の歌詞本文は出力せず、「日本語の和訳詞のみ」を出力すること。前置きや注釈、挨拶は一切不要。
"""

    if context_dict:
        story = context_dict.get("story_and_characters", "")
        if story:
            prompt += f"\n【アルバムの世界観・登場人物・物語】\n{story}\n"

        glossary = context_dict.get("glossary", [])
        if glossary:
            prompt += "\n【重要：このアルバム特有の用語集・ダブルミーニングの指定】\n"
            prompt += "以下の単語が歌詞に登場した場合、直訳せず、指定された意味や文脈、ダブルミーニングを反映した訳文にしてください：\n"
            for item in glossary:
                t = item.get("term", "")
                m = item.get("meaning", "")
                h = item.get("translation_hint", "")
                prompt += f"- 「{t}」: {m}（訳出のヒント: {h}）\n"

    return prompt

def translate_lyrics_safe(
    artist: str,
    title: str,
    album: str,
    original_lyrics: str,
    context_dict: Optional[Dict[str, Any]] = None,
    model_name: Optional[str] = None
) -> Tuple[Optional[str], bool, Optional[str]]:
    """
    英語歌詞を和訳する (v2.1仕様)。
    エラー発生時は translated_lyrics にエラー文字列を混ぜず、第3引数の error_msg として返す。
    戻り値: (translated_text, is_reused, error_msg)
    """
    if not original_lyrics or not original_lyrics.strip():
        return "", False, None

    # 1. 日本語楽曲判定（日本語なら和訳スキップ）
    if not is_likely_western(artist, title):
        return "", False, None

    # 2. 翻訳メモリの完全一致チェック（時間短縮）
    cached = get_cached_translation(original_lyrics)
    if cached:
        return cached, True, None

    # 3. ローカルLLMで翻訳
    model = model_name or select_best_available_model()
    system_prompt = build_translation_system_prompt(artist, album, context_dict)
    user_prompt = f"以下の英語歌詞を、指定された設定と用語集に従って日本語に和訳してください：\n\n{original_lyrics}"

    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_ctx": 4096
        }
    }

    max_retries = 3
    last_err = None

    for attempt in range(max_retries):
        try:
            with requests.Session() as s:
                res = s.post(url, json=payload, timeout=180, headers={"Connection": "close"})
                if res.status_code == 200:
                    data = res.json()
                    translated_text = data.get("response", "").strip()

                    # 4. 翻訳メモリに保存
                    save_translation_memory(artist, title, original_lyrics, translated_text)
                    return translated_text, False, None
                else:
                    last_err = f"HTTP {res.status_code}: {res.text[:200]}"
        except Exception as e:
            last_err = f"ConnectionError: {e}"

        time.sleep(2 * (attempt + 1))

    return None, False, last_err

def translate_lyrics(
    artist: str,
    title: str,
    album: str,
    original_lyrics: str,
    context_dict: Optional[Dict[str, Any]] = None,
    model_name: Optional[str] = None
) -> Tuple[str, bool]:
    """後方互換用ラッパー"""
    if not is_likely_western(artist, title):
        return "【日本語楽曲のため和訳スキップ】", False
    text, reused, err = translate_lyrics_safe(artist, title, album, original_lyrics, context_dict, model_name)
    if err:
        return f"【翻訳エラー】: {err}", False
    return text or "", reused
