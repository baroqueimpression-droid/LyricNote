import os
import re
import json
from typing import Dict, Any, Optional, Tuple, List

def is_live_album_title(album_title: str) -> bool:
    """アルバム名からライブ音源かどうかを簡易判定する"""
    patterns = [r"\blive\b", r"\bin concert\b", r"\btour\b", r"\bbudokan\b", r"\bwoodstock\b", r"\bmtv unplugged\b"]
    return any(re.search(p, album_title, re.IGNORECASE) for p in patterns)

def normalize_track_name(name: str) -> str:
    """曲名から先頭のトラック番号や不要な記号を除去して正規化する"""
    # 先頭の "01. ", "1 - ", "(1) " などを除去
    cleaned = re.sub(r"^\s*\(?\d+\)?[\.\s\-_]+\s*", "", name)
    # 小文字化し、連続空白を圧縮
    cleaned = " ".join(cleaned.lower().split())
    # 記号を除去
    cleaned = re.sub(r"[^\w\s\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", "", cleaned)
    return cleaned.strip()

def build_critic_prompt(artist: str, album: str, track_names: list[str]) -> str:
    """
    Gemini（音楽評論家）に渡すプロンプトを構築する。
    ブラウザのGeminiにそのままコピペして使えるフォーマット。
    """
    is_live = is_live_album_title(album)
    tracks_str = "\n".join([f"- {t}" for t in track_names])

    if is_live:
        focus_instruction = """
【重要：本アルバムはライブアルバムです】
個別の楽曲の歌詞解釈よりも、以下の「ライブとしての価値・文脈」にフォーカスしてください。
1. このツアーの背景、動員数、当時のバンド/アーティストの状況（キャリアの絶頂期、転換期、メンバーの脱退・加入など）。
2. 録音された会場や観客の熱気、特別なアレンジや演出、セットリストの持つ意味。
3. 当時の音楽メディアやファンからの評価、このライブ盤がロック史に与えた影響。
4. このライブ特有の掛け声、MC、またはアレンジ上のキーワードがあれば用語集に記載。
"""
    else:
        focus_instruction = """
【重要：本アルバムはスタジオアルバム/コンセプトアルバムです】
単なる楽曲解説ではなく、深い音楽鑑賞を可能にするため以下の点にフォーカスしてください。
1. 時代背景、当時の社会情勢やカルチャー、アルバム全体のテーマ・コンセプト・メッセージ性。
2. ロックオペラや組曲形式の場合、全体の物語（あらすじ）や架空の登場人物の設定・心理描写。
3. アーティストや作詞家の当時の心情、バンド内の関係性、制作秘話（公式インタビューや音楽誌の信頼性の高い情報に基づく。推測で書かない）。
4. 歌詞に頻出する、または重要な「ダブルミーニング・トリプルミーニング」「慣用句」「造語」「比喩表現」を抽出し、用語集として定義してください。※後続の翻訳工程で正確に反映させるために極めて重要です。
"""

    return f"""あなたは世界最高峰の音楽評論家・ライナーノーツ執筆者です。
ファンサイトの推測ではなく、公式インタビューや信頼性の高い音楽誌・バイオグラフィに基づき、以下のアルバムに関する詳細なライナーノーツおよび翻訳用コンテキストを作成してください。

アーティスト: {artist}
アルバム: {album}
収録曲:
{tracks_str}

{focus_instruction}

出力は必ず以下のJSONフォーマットのみ（マークダウンの ```json ... ``` で囲む）で出力してください。余計な前置きや挨拶は不要です。

```json
{{
  "is_live": {"true" if is_live else "false"},
  "album_overview": "（ここに音楽ファンが深く感動できるような、アルバム全体の解説。1000〜2000文字程度）",
  "track_commentaries": [
    {{
      "track_name": "（収録曲名。上記リストと完全に一致させること）",
      "commentary": "（この楽曲独自の解説、制作背景、エピソード、歌詞の深い解釈。300〜600文字程度）"
    }}
  ],
  "story_and_characters": "（コンセプトアルバムや物語がある場合、世界観やキャラクター設定のまとめ。ない場合は空文字）",
  "glossary": [
    {{
      "term": "（歌詞に出てくる重要な単語・造語・比喩）",
      "meaning": "（直訳ではなく、このアルバムの文脈における意味、ダブルミーニングの解説）",
      "translation_hint": "（和訳する際にどう表現すべきかのアドバイス）"
    }}
  ]
}}
```
"""

def parse_critic_output(raw_text: str) -> Optional[Dict[str, Any]]:
    """Geminiの出力テキストからJSONを安全にパースする"""
    try:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            if start != -1 and end != -1:
                json_str = raw_text[start:end+1]
            else:
                json_str = raw_text

        data = json.loads(json_str)
        if ("album_overview" in data or "liner_notes" in data) and "glossary" in data:
            return data
        return None
    except Exception as e:
        print(f"JSONパースエラー: {e}")
        return None

def validate_critic_output(data: Dict[str, Any], expected_tracks: Optional[List[str]] = None) -> Tuple[bool, str]:
    """
    解説JSONが仕様を満たしているか検証する (v2.1)
    """
    if not isinstance(data, dict):
        return False, "出力がJSONオブジェクトではありません。"

    overview = data.get("album_overview") or data.get("liner_notes")
    if not overview or not overview.strip():
        return False, "アルバム全体解説（album_overview）が存在しません。"

    commentaries = data.get("track_commentaries")
    if commentaries is not None and not isinstance(commentaries, list):
        return False, "楽曲個別解説（track_commentaries）が配列形式ではありません。"

    if expected_tracks and commentaries:
        # トラック網羅度の簡易チェック
        found_names = [tc.get("track_name", "") for tc in commentaries if isinstance(tc, dict)]
        normalized_found = [normalize_track_name(fn) for fn in found_names]
        unmatched = []
        for exp in expected_tracks:
            norm_exp = normalize_track_name(exp)
            if not any(norm_exp in nf or nf in norm_exp for nf in normalized_found if nf):
                unmatched.append(exp)
        if len(unmatched) > len(expected_tracks) // 2:
            return False, f"半数以上の楽曲解説がマッチしませんでした (未一致例: {unmatched[:3]})"

    return True, "OK"

def match_track_commentaries(commentaries: List[Dict[str, Any]], tracks: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    track_commentaries のリストと DBトラックリストを正規化照合し、
    {persistent_id: commentary} のマッピングを返す
    """
    result = {}
    if not commentaries:
        return result

    # 正規化インデックスを作成
    norm_comments = []
    for item in commentaries:
        if isinstance(item, dict):
            raw_name = item.get("track_name", "")
            comm = item.get("commentary", "")
            if comm:
                norm_comments.append((normalize_track_name(raw_name), comm, raw_name))

    for t in tracks:
        pid = t["persistent_id"]
        t_name = t["name"]
        norm_t_name = normalize_track_name(t_name)

        matched_comm = None
        # 1. 完全一致
        for n_c_name, comm, _ in norm_comments:
            if norm_t_name == n_c_name and n_c_name:
                matched_comm = comm
                break

        # 2. 包含一致（"01. " などの揺れ）
        if not matched_comm:
            for n_c_name, comm, _ in norm_comments:
                if (norm_t_name in n_c_name or n_c_name in norm_t_name) and (n_c_name and norm_t_name):
                    matched_comm = comm
                    break

        if matched_comm:
            result[pid] = matched_comm

    return result

def call_gemini_api_safe(prompt: str, api_key: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Gemini APIを呼び出す。
    Rate Limit超過時は即座に停止し、エラーメッセージを返す（追加課金を絶対に発生させない）。
    """
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None, "Gemini APIキーが設定されていません。手動コピペモードをご利用ください。"

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        
        parsed = parse_critic_output(response.text)
        if parsed:
            is_valid, msg = validate_critic_output(parsed)
            if is_valid:
                return parsed, "OK"
            return parsed, f"警告付きOK: {msg}"
        else:
            return None, "出力結果のJSONパースに失敗しました。"

    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
            return None, "【API制限検知】無料枠のリミットに達しました。追加課金を防ぐため停止しました。手動コピペモードをご利用ください。"
        return None, f"Gemini API呼び出しエラー: {err_msg}"
