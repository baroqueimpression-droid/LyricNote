import json
from pathlib import Path
from src.config import BASE_DATA_DIR, BACKUP_DIR
from src.db import (
    init_db, upsert_track, upsert_album_context, get_album_tracks,
    save_translation_memory, get_cached_translation
)
from src.critic import build_critic_prompt, parse_critic_output, is_live_album_title
from src.translator import build_translation_system_prompt, translate_lyrics
from src.itunes_writer import format_combined_lyrics

def run_tests():
    print("=== Lylic 総合テスト開始 ===")
    init_db()

    # 1. ライブアルバム判定テスト
    assert is_live_album_title("Yessongs (Live)") == True
    assert is_live_album_title("Close to the Edge") == False
    print("[OK] Live album detection")

    # 2. 音楽評論家（Gemini）プロンプト構築＆JSONパーステスト
    prompt = build_critic_prompt("Genesis", "The Lamb Lies Down on Broadway", ["The Lamb Lies Down on Broadway", "Fly on a Windshield"])
    assert "ダブルミーニング" in prompt
    assert "用語集" in prompt
    print("[OK] Gemini prompt builder")

    sample_gemini_output = """
```json
{
  "is_live": false,
  "liner_notes": "本作はピーター・ガブリエル率いるジェネシスの最高峰ロックオペラである。",
  "story_and_characters": "主人公ラエルがニューヨークの地下迷宮を彷徨う物語。",
  "glossary": [
    {
      "term": "Lamb",
      "meaning": "無垢、犠牲の象徴。ブロードウェイに横たわる違和感。",
      "translation_hint": "単なる羊ではなく『子羊（生贄）』のニュアンスを含めること"
    }
  ]
}
```
"""
    parsed = parse_critic_output(sample_gemini_output)
    assert parsed is not None
    assert len(parsed["glossary"]) == 1
    assert parsed["glossary"][0]["term"] == "Lamb"
    print("[OK] Gemini JSON parser")

    # 3. 翻訳職人（Gemma 2）動的システムプロンプト構築テスト
    sys_prompt = build_translation_system_prompt("Genesis", "The Lamb Lies Down on Broadway", parsed)
    assert "主人公ラエル" in sys_prompt
    assert "子羊（生贄）" in sys_prompt
    print("[OK] Dynamic system prompt (Story + Glossary)")

    # 4. 日本語楽曲スキップ判定テスト
    ja_result, _ = translate_lyrics("サザンオールスターズ", "いとしのエリー", "10ナンバーズ・からっと", "みつめあう瞳と瞳")
    assert "【日本語楽曲のため和訳スキップ】" in ja_result
    print("[OK] Japanese lyrics skip")

    # 5. 翻訳メモリ（重複曲の時間短縮）テスト
    save_translation_memory("Queen", "Bohemian Rhapsody", "Is this the real life?", "これは現実なのか？")
    cached = get_cached_translation("Is this the real life?")
    assert cached == "これは現実なのか？"
    
    # 大文字小文字や空白の差もハッシュで吸収されるか
    cached2 = get_cached_translation("  is this the REAL life? \n")
    assert cached2 == "これは現実なのか？"
    print("[OK] Translation memory (deduplication)")

    # 6. フォーマット整形テスト
    formatted = format_combined_lyrics(
        original_lyrics="Is this the real life?",
        translated_lyrics="これは現実なのか？",
        album_overview="クイーンの代表作。"
    )
    assert "【Original Lyrics】" in formatted
    assert "【和訳】" in formatted
    assert "【アルバム解説】" in formatted
    print("[OK] Lyrics format combiner")

    print("\nALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
