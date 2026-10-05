"""試作③ ローカルLLM 和訳比較。

使い方:
  python 03_translate_compare.py [--n 5] [--models m1 m2 ...] [--pids PID ...]

- 試作②で LRCLIB から歌詞を取得できた英語曲を対象に、複数モデルで和訳する。
- 2段階: Pass1 = 楽曲分析 (テーマ・語り手・スラング/慣用句) → Pass2 = 行番号付き JSON で全行和訳
- 出力レイアウト: 英詞(原文) → 和訳詞(同じ連構成) → 解説
- 和訳結果は DATA_DIR/translate_compare/<model>/ に保存(私的利用・非共有)。
  画面と summary.csv には速度・行の欠落などの「構造指標」だけを出す。品質は人間が目で確認する。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path

import requests

from common import LRCLIB_CACHE_DIR, TRACKS_CSV, TRANSLATE_OUT_DIR

OLLAMA = "http://localhost:11434/api/chat"
DEFAULT_MODELS = ["hf.co/mmnga/ABEJA-Qwen2.5-7b-Japanese-v0.1-gguf:Q4_K_M"]
OPTIONS = {"temperature": 0.3, "top_p": 0.9, "num_ctx": 8192, "num_predict": 4096}
_JA_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")

SYSTEM = (
    "あなたは洋楽に精通した音楽評論家・翻訳家です。"
    "ユーザーが個人で楽しむために、所有する楽曲の歌詞を日本語に訳します。"
    "直訳ではなく、時代背景・スラング・慣用句・比喩を踏まえ、日本語の歌詞として自然に読める訳にしてください。"
)

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "theme": {"type": "string", "description": "曲全体のテーマ(日本語・2〜3文)"},
        "narrator": {"type": "string", "description": "語り手と相手の関係・視点"},
        "mood": {"type": "string"},
        "expressions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "phrase": {"type": "string", "description": "スラング/慣用句/比喩 (原文の数語のみ)"},
                    "meaning": {"type": "string", "description": "日本語での意味・ニュアンス"},
                },
                "required": ["phrase", "meaning"],
            },
        },
    },
    "required": ["theme", "narrator", "mood", "expressions"],
}

TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"n": {"type": "integer"}, "ja": {"type": "string"}},
                "required": ["n", "ja"],
            },
        }
    },
    "required": ["lines"],
}


def chat(model: str, user: str, schema: dict) -> tuple[dict | None, dict]:
    body = {
        "model": model,
        "stream": False,
        "format": schema,
        "options": OPTIONS,
        "keep_alive": "10m",
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    }
    t0 = time.time()
    res = requests.post(OLLAMA, json=body, timeout=900)
    res.raise_for_status()
    data = res.json()
    m = {
        "wall_s": time.time() - t0,
        "out_tokens": data.get("eval_count", 0),
        "tok_per_s": data.get("eval_count", 0) / max(data.get("eval_duration", 1) / 1e9, 1e-9),
    }
    try:
        return json.loads(data["message"]["content"]), m
    except Exception:
        return None, m


def meta_text(r: dict) -> str:
    return f"曲名: {r['name']}\nアーティスト: {r['artist']}\nアルバム: {r['album']}\n発表年: {r['year'] or '不明'}\nジャンル: {r['genre'] or '不明'}"


def translate_song(model: str, r: dict, lyrics: str) -> tuple[str, dict]:
    src_lines = lyrics.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    numbered = [(i, s) for i, s in enumerate(src_lines) if s.strip()]
    numbered_text = "\n".join(f"{k}: {s}" for k, (_, s) in enumerate(numbered, 1))

    # Pass 1: 分析
    analysis, m1 = chat(model, (
        f"{meta_text(r)}\n\n以下の歌詞を分析し、テーマ・語り手・ムード・訳出に注意すべき表現(スラング/慣用句/比喩/時代特有の言い回し)をJSONで答えてください。\n\n"
        f"---歌詞---\n{lyrics}"
    ), ANALYSIS_SCHEMA)

    # Pass 2: 行単位の和訳
    hints = ""
    if analysis:
        hints = f"テーマ: {analysis.get('theme', '')}\n語り手: {analysis.get('narrator', '')}\n注意表現:\n" + "\n".join(
            f"- {e.get('phrase')}: {e.get('meaning')}" for e in analysis.get("expressions", [])
        )
    trans, m2 = chat(model, (
        f"{meta_text(r)}\n\n{hints}\n\n"
        f"以下の番号付き歌詞(全{len(numbered)}行)を、番号を保ったまま1行ずつ日本語に訳してください。"
        "全ての番号について必ず1つずつ訳を返し、行を統合・省略しないでください。繰り返しの行も毎回訳してください。\n\n"
        f"{numbered_text}"
    ), TRANSLATION_SCHEMA)

    ja_by_n = {}
    if trans:
        for item in trans.get("lines", []):
            try:
                ja_by_n[int(item["n"])] = str(item["ja"]).strip()
            except Exception:
                pass

    # 原文と同じ連構成(空行位置)で和訳詞を組み立てる
    ja_lines, k = [], 0
    for s in src_lines:
        if s.strip():
            k += 1
            ja_lines.append(ja_by_n.get(k, "〔訳抜け〕"))
        else:
            ja_lines.append("")

    notes = ["【解説】"]
    if analysis:
        notes += [analysis.get("theme", ""), f"語り手: {analysis.get('narrator', '')}", f"ムード: {analysis.get('mood', '')}"]
        if analysis.get("expressions"):
            notes.append("")
            notes.append("◆ 表現メモ")
            notes += [f"・{e.get('phrase')} … {e.get('meaning')}" for e in analysis["expressions"]]
    sep = "――――――――――――"
    text = "\n".join(src_lines + ["", sep, "【和訳】"] + ja_lines + ["", sep] + notes)

    n_src = len(numbered)
    got = [ja_by_n.get(i) for i in range(1, n_src + 1)]
    metrics = {
        "src_lines": n_src,
        "missing": sum(1 for g in got if not g),
        "extra": max(0, len(ja_by_n) - n_src),
        "no_japanese": sum(1 for g in got if g and not _JA_RE.search(g)),
        "analysis_ok": analysis is not None,
        "expressions": len(analysis.get("expressions", [])) if analysis else 0,
        "pass1_s": round(m1["wall_s"], 1),
        "pass2_s": round(m2["wall_s"], 1),
        "tok_per_s": round(m2["tok_per_s"], 1),
    }
    return text, metrics


def load_targets(n: int, pids: list[str] | None) -> list[tuple[dict, str]]:
    with open(TRACKS_CSV, encoding="utf-8-sig") as f:
        rows = {r["pid"]: r for r in csv.DictReader(f)}
    out = []
    files = [LRCLIB_CACHE_DIR / f"{p}.json" for p in pids] if pids else sorted(LRCLIB_CACHE_DIR.glob("*.json"))
    for fp in files:
        if not fp.exists():
            continue
        d = json.loads(fp.read_text(encoding="utf-8"))
        res = d.get("result") or {}
        r = rows.get(d["pid"])
        if r and r["lang"] == "en" and res.get("plainLyrics") and not res.get("instrumental"):
            out.append((r, res["plainLyrics"]))
        if len(out) >= n:
            break
    return out


def safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", s)[:80]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    p.add_argument("--pids", nargs="*")
    args = p.parse_args()

    targets = load_targets(args.n, args.pids)
    print(f"対象 {len(targets)}曲 × モデル {len(args.models)}")
    summary = []
    for model in args.models:
        outdir = TRANSLATE_OUT_DIR / safe(model)
        outdir.mkdir(parents=True, exist_ok=True)
        print(f"\n## {model}")
        for r, lyrics in targets:
            try:
                text, m = translate_song(model, r, lyrics)
            except Exception as e:
                print(f"  ERROR {r['artist']} - {r['name']}: {e}")
                continue
            (outdir / f"{safe(r['artist'])} - {safe(r['name'])}.txt").write_text(text, encoding="utf-8")
            m.update({"model": model, "artist": r["artist"], "title": r["name"], "pid": r["pid"]})
            summary.append(m)
            print(
                f"  {r['artist']} - {r['name']}: {m['src_lines']}行 欠落{m['missing']} 余剰{m['extra']} 未訳{m['no_japanese']}"
                f" 表現{m['expressions']} | {m['pass1_s']}s+{m['pass2_s']}s {m['tok_per_s']}tok/s"
            )

    if summary:
        sp = TRANSLATE_OUT_DIR / "summary.csv"
        with open(sp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
            w.writeheader()
            w.writerows(summary)
        print(f"\n出力: {TRANSLATE_OUT_DIR}  (summary.csv / <model>/*.txt)")


if __name__ == "__main__":
    main()
