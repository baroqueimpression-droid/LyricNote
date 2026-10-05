import sys
import os
import argparse
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Windows Python 3.13 における asyncio ProactorEventLoop の WinError 10054 / 10022 対策
if sys.platform == "win32":
    import asyncio
    import asyncio.proactor_events

    orig_call_conn_lost = asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost
    def _safe_call_connection_lost(self, exc=None):
        try:
            orig_call_conn_lost(self, exc)
        except OSError as e:
            if getattr(e, "winerror", None) in (10022, 10054):
                pass
            else:
                raise
    asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost = _safe_call_connection_lost

import json
import socket
from pathlib import Path
import gradio as gr
from typing import List, Dict, Any, Tuple, Optional
import qrcode

from src.config import RECOMMENDED_MODEL, FALLBACK_MODEL, DEFAULT_SERVER_PORT
from src.db import (
    init_db, get_album_list, get_album_tracks, upsert_album_context,
    update_track_lyrics, update_track_translation, update_track_liner_notes,
    get_connection
)
from src.scanner import scan_itunes_to_db
from src.ollama_manager import list_installed_models, pull_model_stream, is_model_installed
from src.critic import (
    build_critic_prompt, parse_critic_output, match_track_commentaries,
    validate_critic_output
)
from src.lyrics_fetcher import fetch_lyrics_multistage
from src.translator import translate_lyrics_safe, select_best_available_model
from src.itunes_writer import format_combined_lyrics, write_to_itunes, restore_from_backup
from src.batch_runner import BatchRunner

# DB初期化 (v2.1マイグレーション含む)
init_db()

# ガイド読み込み
USER_GUIDE_PATH = Path(__file__).resolve().parent.parent / "USER_GUIDE.md"
def load_user_guide() -> str:
    if USER_GUIDE_PATH.exists():
        try:
            with open(USER_GUIDE_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return "ユーザーズガイドの読み込みに失敗しました。"

def get_local_ip() -> str:
    """ローカルLANのIPアドレスを取得する"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ==============================================================================
# UI コントローラ
# ==============================================================================
def refresh_album_dropdown():
    albums = get_album_list()
    choices = [
        f"{a['artist']} - {a['album']} [Ready: {a.get('ready_count', 0)}/{a['track_count']}, 書込済: {a['completed_count']}]"
        for a in albums
    ]
    return gr.update(choices=choices, value=choices[0] if choices else None)

def on_scan_itunes(progress=gr.Progress()):
    def cb(current, total, msg):
        progress((current, total), desc=msg)
    stats = scan_itunes_to_db(progress_callback=cb)
    msg = f"スキャン完了: 合計{stats['total_scanned']}件, 更新{stats['added_or_updated']}件, スキップ{stats['skipped']}件"
    return msg

def on_download_gemma(progress=gr.Progress()):
    for status, p in pull_model_stream(RECOMMENDED_MODEL):
        if p >= 0:
            progress(p, desc=f"{RECOMMENDED_MODEL}: {status}")
        else:
            return f"エラー: {status}", gr.update(), gr.update()
    
    new_choices = list_installed_models()
    return (
        f"{RECOMMENDED_MODEL} の準備が完了しました！",
        gr.update(choices=new_choices, value=RECOMMENDED_MODEL),
        gr.update(value=f"✅ {RECOMMENDED_MODEL} インストール済み", interactive=False)
    )

def parse_album_choice(choice_str: str) -> Tuple[str, str]:
    if not choice_str:
        return "", ""
    clean = choice_str.split(" [")[0].split(" (")[0]
    parts = clean.split(" - ")
    if len(parts) >= 2:
        return parts[0].strip(), " - ".join(parts[1:]).strip()
    return "", ""

def on_select_album(choice_str: str):
    artist, album = parse_album_choice(choice_str)
    if not artist or not album:
        return "", "", "", gr.update(choices=[]), "", "", "", "", ""

    tracks = get_album_tracks(artist, album)
    track_choices = [f"{t['track_number']:02d}. {t['name']}" for t in tracks]

    conn = get_connection()
    album_row = conn.execute("SELECT * FROM albums WHERE album_id = ?", (f"{artist}:{album}",)).fetchone()
    conn.close()

    liner_notes = album_row["liner_notes"] if album_row and album_row["liner_notes"] else ""
    context_json = album_row["context_json"] if album_row and album_row["context_json"] else ""

    info = f"### 🎵 {artist} - {album}\n収録曲数: {len(tracks)} 曲"
    first_track_choice = track_choices[0] if track_choices else None
    
    first_orig, first_trans, first_track_comm, first_status = "", "", "", ""
    if tracks:
        first_orig = tracks[0]["original_lyrics"] or ""
        first_trans = tracks[0]["translated_lyrics"] or ""
        first_track_comm = tracks[0]["liner_notes"] or ""
        first_status = f"Status: {tracks[0]['status']} | Error: {tracks[0].get('last_error') or 'None'}"

    return (
        info, liner_notes, context_json,
        gr.update(choices=track_choices, value=first_track_choice),
        first_orig, first_trans, first_track_comm, first_status, ""
    )

def on_generate_critic_prompt(choice_str: str):
    artist, album = parse_album_choice(choice_str)
    if not artist:
        return "アルバムが選択されていません。"
    tracks = get_album_tracks(artist, album)
    track_names = [t["name"] for t in tracks]
    return build_critic_prompt(artist, album, track_names)

def on_apply_critic_json(choice_str: str, raw_json_str: str):
    artist, album = parse_album_choice(choice_str)
    if not artist:
        return "アルバムを選択してください。", "", ""
    parsed = parse_critic_output(raw_json_str)
    if not parsed:
        return "JSONの解析に失敗しました。正しいフォーマットか確認してください。", "", ""

    liner_notes = parsed.get("album_overview", parsed.get("liner_notes", ""))
    is_live = parsed.get("is_live", False)
    upsert_album_context(artist, album, liner_notes, json.dumps(parsed, ensure_ascii=False), is_live=is_live)

    # 各曲個別解説のDB永続化 (v2.1仕様)
    tracks = get_album_tracks(artist, album)
    commentaries = parsed.get("track_commentaries", [])
    matched_map = match_track_commentaries(commentaries, tracks)
    for pid, comm in matched_map.items():
        update_track_liner_notes(pid, comm)

    msg = f"アルバム解説および各曲解説（{len(matched_map)}/{len(tracks)}曲マッチ）をDBに永続化保存しました！"
    return msg, liner_notes, json.dumps(parsed, ensure_ascii=False, indent=2)

def on_select_track(choice_str: str, track_choice_str: str):
    artist, album = parse_album_choice(choice_str)
    if not track_choice_str:
        return "", "", "", ""
    try:
        t_no = int(track_choice_str.split(".")[0])
    except Exception:
        return "", "", "", ""
    tracks = get_album_tracks(artist, album)
    selected = next((t for t in tracks if t["track_number"] == t_no), None)
    if selected:
        status_disp = f"Status: {selected['status']} | Retry: {selected.get('retry_count', 0)} | LastError: {selected.get('last_error') or 'None'}"
        return selected["original_lyrics"] or "", selected["translated_lyrics"] or "", selected["liner_notes"] or "", status_disp
    return "", "", "", ""

def on_save_manual_track_data(choice_str: str, track_choice_str: str, orig_lyrics: str, trans_lyrics: str, track_comm: str):
    """手動入力された歌詞・和訳・解説をDBに保存する (UC-5対応)"""
    artist, album = parse_album_choice(choice_str)
    if not track_choice_str:
        return "曲を選択してください。"
    t_no = int(track_choice_str.split(".")[0])
    tracks = get_album_tracks(artist, album)
    selected = next((t for t in tracks if t["track_number"] == t_no), None)
    if not selected:
        return "曲が見つかりません。"

    pid = selected["persistent_id"]
    is_ja = (selected["language"] == "ja")

    if orig_lyrics and orig_lyrics.strip():
        update_track_lyrics(pid, orig_lyrics.strip(), source="manual")
    if trans_lyrics and trans_lyrics.strip():
        update_track_translation(pid, trans_lyrics.strip(), liner_notes=track_comm)
    elif track_comm:
        update_track_liner_notes(pid, track_comm)

    # 歌詞が存在すれば ready_to_write に昇格
    if orig_lyrics and orig_lyrics.strip() and (is_ja or (trans_lyrics and trans_lyrics.strip())):
        conn = get_connection()
        conn.execute("UPDATE tracks SET status = 'ready_to_write', last_error = NULL WHERE persistent_id = ?", (pid,))
        conn.commit()
        conn.close()

    return "楽曲データを手動保存しました！（書き込み可能状態に更新）"

def on_write_track_itunes(choice_str: str, track_choice_str: str, orig_lyrics: str, trans_lyrics: str, track_comm: str, album_overview: str):
    artist, album = parse_album_choice(choice_str)
    if not track_choice_str:
        return "曲を選択してください。"
    t_no = int(track_choice_str.split(".")[0])
    tracks = get_album_tracks(artist, album)
    selected = next((t for t in tracks if t["track_number"] == t_no), None)
    if not selected:
        return "対象トラックが見つかりません。"

    is_ja = (selected["language"] == "ja")
    formatted = format_combined_lyrics(
        original_lyrics=orig_lyrics,
        translated_lyrics=trans_lyrics,
        track_commentary=track_comm,
        album_overview=album_overview,
        track_name=selected["name"],
        is_japanese=is_ja
    )
    success, msg = write_to_itunes(selected["persistent_id"], formatted)
    return msg

def on_write_album_itunes(choice_str: str, album_overview: str, progress=gr.Progress()):
    """安全ガードレール付きアルバム書き込み: ready_to_write / completed の曲のみ反映"""
    artist, album = parse_album_choice(choice_str)
    if not artist:
        return "アルバムを選択してください。"
    tracks = get_album_tracks(artist, album)
    total = len(tracks)

    success_count = 0
    skipped_count = 0

    for i, t in enumerate(tracks):
        progress((i, total), desc=f"iTunes書き込み中 ({i+1}/{total}): {t['name']}")
        
        # 安全防壁: 歌詞がない、または未完了曲は除外
        orig = t["original_lyrics"] or ""
        if not orig or t["status"] not in ("ready_to_write", "completed"):
            skipped_count += 1
            continue

        is_ja = (t["language"] == "ja")
        formatted = format_combined_lyrics(
            original_lyrics=orig,
            translated_lyrics=t["translated_lyrics"] or "",
            track_commentary=t["liner_notes"] or "",
            album_overview=album_overview,
            track_name=t["name"],
            is_japanese=is_ja
        )
        ok, _ = write_to_itunes(t["persistent_id"], formatted)
        if ok:
            success_count += 1

    return f"書き込み完了: {success_count}/{total} 曲をiTunesに反映しました（未完了・歌詞なしの {skipped_count} 曲は安全のため除外）。"

# ==============================================================================
# UI ビルド (v2.1 Phase 3専用コンソール + 既存機能完全維持)
# ==============================================================================
local_ip = get_local_ip()
mobile_url = f"http://{local_ip}:{DEFAULT_SERVER_PORT}"

qr = qrcode.QRCode(box_size=5, border=3)
qr.add_data(mobile_url)
qr.make(fit=True)
qr_img = qr.make_image(fill_color="black", back_color="white")

with gr.Blocks(title="LyricNote - 楽曲解説・和訳ライナーノーツ") as app:
    gr.Markdown("# 🎵 LyricNote (リリックノート) v2.1\n### 音楽の魂と物語を深く味わう、iTunes連携・AIライナーノーツシステム")

    with gr.Tabs():
        # タブ1: アルバム管理 & 設定
        with gr.TabItem("📁 アルバム管理 & 設定"):
            with gr.Row():
                with gr.Column(scale=2):
                    gr.Markdown("### 📱 スマホ接続情報")
                    gr.Markdown(f"同じWi-Fi、またはTailscale VPN環境でアクセス：\n**`{mobile_url}`**")
                    gr.Image(value=qr_img.get_image(), show_label=False, width=220, interactive=False)
                with gr.Column(scale=3):
                    gr.Markdown("### ⚙️ iTunesライブラリ同期")
                    scan_btn = gr.Button("🔄 iTunesライブラリを差分スキャン", variant="primary")
                    scan_status = gr.Textbox(label="スキャン状態", interactive=False)
                    scan_btn.click(fn=on_scan_itunes, outputs=[scan_status])

                    gr.Markdown("---")
                    gr.Markdown(f"### 🤖 推奨ローカルモデル: `{RECOMMENDED_MODEL}`")
                    installed_list = list_installed_models()
                    gemma_ready = is_model_installed(RECOMMENDED_MODEL)
                    
                    m_status = "インストール済み ✅" if gemma_ready else f"未インストール ❌ ({', '.join(installed_list) or 'Ollama未検出'})"
                    model_status = gr.Textbox(label="モデル状態", value=m_status, interactive=False)
                    download_btn = gr.Button("⬇️ 推奨モデル(Gemma 2 9B)を自動ダウンロード", variant="secondary", interactive=not gemma_ready)
                    download_btn.click(fn=on_download_gemma, outputs=[model_status, download_btn])

        # タブ2: プレビュー & iTunes反映コンソール (Phase 3)
        with gr.TabItem("🎧 プレビュー & iTunes書き込み"):
            with gr.Row():
                album_dropdown = gr.Dropdown(label="アルバム選択", choices=[], interactive=True)
                refresh_btn = gr.Button("🔄", scale=0)
                refresh_btn.click(fn=refresh_album_dropdown, outputs=[album_dropdown])

            album_info_md = gr.Markdown("アルバムを選択してください。")

            with gr.Accordion("1. アルバム全体解説 & コンテキスト設定", open=False):
                gr.Markdown("※ エージェントによる全自動実行済みデータ、または手動Gemini貼り付けデータが表示されます。")
                with gr.Row():
                    gen_prompt_btn = gr.Button("📋 手動用プロンプト生成", variant="secondary")
                    copy_prompt_btn = gr.Button("📄 クリップボードにコピー", variant="secondary")
                prompt_output = gr.Textbox(label="生成されたプロンプト", lines=4)
                gen_prompt_btn.click(fn=on_generate_critic_prompt, inputs=[album_dropdown], outputs=[prompt_output])
                copy_prompt_btn.click(
                    None, inputs=[prompt_output],
                    js="""(text) => { navigator.clipboard.writeText(text); alert("コピーしました！"); }"""
                )

                with gr.Row():
                    paste_json_input = gr.Textbox(label="Geminiからの出力JSON貼り付け（手動モード用）", lines=4)
                apply_json_btn = gr.Button("💾 設定・用語集を適用 & 全曲解説をDB保存", variant="primary")
                json_status = gr.Textbox(label="適用ステータス", interactive=False)

                with gr.Row():
                    album_overview_box = gr.Textbox(label="アルバム全体解説 / ライナーノーツ", lines=6)
                    context_json_box = gr.Textbox(label="翻訳用コンテキスト・用語集 (JSON)", lines=6)

                apply_json_btn.click(
                    fn=on_apply_critic_json,
                    inputs=[album_dropdown, paste_json_input],
                    outputs=[json_status, album_overview_box, context_json_box]
                )

            with gr.Accordion("2. 楽曲プレビュー & 手動微修正 (Phase 3 安全確認)", open=True):
                track_dropdown = gr.Dropdown(label="収録曲", choices=[], interactive=True)
                track_status_box = gr.Textbox(label="楽曲ステータス & エラー詳細", interactive=False)

                with gr.Row():
                    orig_lyrics_box = gr.Textbox(label="Original Lyrics (原文歌詞 / 歌詞空白時はここに入力して保存可能)", lines=10)
                    trans_lyrics_box = gr.Textbox(label="和訳歌詞 (手動微修正可能 / 邦楽は空)", lines=10)

                with gr.Row():
                    track_comm_box = gr.Textbox(label="【この曲の個別解説】 (手動修正可能)", lines=6)

                with gr.Row():
                    save_track_btn = gr.Button("💾 この曲の修正データをDB保存", variant="secondary")
                    save_track_msg = gr.Textbox(label="保存状態", interactive=False, scale=2)
                save_track_btn.click(
                    fn=on_save_manual_track_data,
                    inputs=[album_dropdown, track_dropdown, orig_lyrics_box, trans_lyrics_box, track_comm_box],
                    outputs=[save_track_msg]
                )

            with gr.Accordion("3. iTunes書き込み実行 (安全ガードレール適用)", open=True):
                with gr.Row():
                    write_track_btn = gr.Button("💾 この曲をiTunesに書き込む", variant="primary")
                    write_album_btn = gr.Button("🚀 アルバム全曲を一括書き込み (Ready曲のみ安全反映)", variant="primary")
                
                write_result = gr.Textbox(label="書込結果", interactive=False)
                write_track_btn.click(
                    fn=on_write_track_itunes,
                    inputs=[album_dropdown, track_dropdown, orig_lyrics_box, trans_lyrics_box, track_comm_box, album_overview_box],
                    outputs=[write_result]
                )
                write_album_btn.click(
                    fn=on_write_album_itunes,
                    inputs=[album_dropdown, album_overview_box],
                    outputs=[write_result]
                )

            # 連動イベント
            album_dropdown.change(
                fn=on_select_album,
                inputs=[album_dropdown],
                outputs=[
                    album_info_md, album_overview_box, context_json_box,
                    track_dropdown, orig_lyrics_box, trans_lyrics_box, track_comm_box,
                    track_status_box, write_result
                ]
            )
            track_dropdown.change(
                fn=on_select_track,
                inputs=[album_dropdown, track_dropdown],
                outputs=[orig_lyrics_box, trans_lyrics_box, track_comm_box, track_status_box]
            )

        # タブ3: 洋楽ライナーノーツ辞典 (閲覧機能)
        with gr.TabItem("📖 洋楽ライナーノーツ辞典"):
            gr.Markdown("### 📚 蓄積されたアルバム・ライナーノーツ辞典\n通勤中やお好みの時間に、アルバムの背景や物語を閲覧できます。")
            dict_album_dropdown = gr.Dropdown(label="アルバムを選択", choices=[], interactive=True)
            dict_content = gr.Markdown("アルバムを選択するとライナーノーツが表示されます。")

            def on_select_dict_album(choice_str: str):
                artist, album = parse_album_choice(choice_str)
                if not artist:
                    return ""
                conn = get_connection()
                row = conn.execute("SELECT * FROM albums WHERE album_id = ?", (f"{artist}:{album}",)).fetchone()
                tracks = conn.execute("SELECT name, track_number, liner_notes FROM tracks WHERE artist = ? AND album = ? ORDER BY track_number", (artist, album)).fetchall()
                conn.close()
                if row and row["liner_notes"]:
                    is_live_str = "【LIVE ALBUM】" if row["is_live"] else "【STUDIO ALBUM】"
                    body = f"# {is_live_str} {artist} - {album}\n\n## アルバム解説\n{row['liner_notes']}\n\n---\n## 収録曲の解説\n"
                    for t in tracks:
                        if t["liner_notes"]:
                            body += f"### {t['track_number']:02d}. {t['name']}\n{t['liner_notes']}\n\n"
                    return body
                return "このアルバムのライナーノーツはまだ作成されていません。"

            dict_album_dropdown.change(fn=on_select_dict_album, inputs=[dict_album_dropdown], outputs=[dict_content])

        # タブ4: ユーザーズガイド
        with gr.TabItem("💡 ユーザーズガイド"):
            guide_md = gr.Markdown(value=load_user_guide())

    # 起動時のドロップダウン初期ロード
    app.load(fn=refresh_album_dropdown, outputs=[album_dropdown])
    app.load(fn=refresh_album_dropdown, outputs=[dict_album_dropdown])

def main():
    port = DEFAULT_SERVER_PORT

    print("\n" + "=" * 65)
    print("  [LyricNote] 洋楽和訳・ライナーノーツシステム v2.1")
    print(f"  PCから操作:     http://localhost:{port}")
    print(f"  スマホアクセス: {mobile_url}")
    print("  ※ 外部からの安全なアクセスにはTailscale VPN等の利用を推奨します。")
    print("=" * 65)
    print("ブラウザを自動で開きます...\n")

    app.launch(
        server_name="0.0.0.0",
        server_port=port,
        inbrowser=True
    )

if __name__ == "__main__":
    main()
