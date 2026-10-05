# LyricNote (リリックノート)

iTunes/Apple Musicのライブラリに蓄積された洋楽・邦楽資産に対し、「格調高い和訳」と「深いライナーノーツ（楽曲個別解説＋アルバム全体解説）」を自律的に生成・付与し、音楽鑑賞体験を極限まで豊かにするための自律型アシスタントシステムです。

---

## 主な機能

1. **エージェント完全自動バッチ実行 (Pass 1〜3)**:
   - **Pass 1 (全曲走破)**: ライブラリ全曲（数千曲・数百アルバム）をノンストップで走破し、解説生成・歌詞取得・和訳をDBに蓄積。
   - **Pass 2 (自動差分リトライ)**: 偶発的エラー曲を自動検出し、ユーザー確認不要で1回差分リトライ。
   - **Pass 3 (自律深層診断)**: 2回失敗曲の原因を自動分類（WEB上に歌詞未存在、表記揺れ自己修復等）しレポート出力。
2. **多段階歌詞取得エンジン**:
   - iTunes既存歌詞（最優先） $\to$ LRCLIB完全一致 $\to$ 表記揺れ正規化あいまい検索。
3. **安全ガードレール付き iTunes 書き込み (Phase 3)**:
   - エージェントが勝手にiTunesを更新することは絶対にせず、WebUIからユーザーの意思で書き込み実行。
   - 未完了・エラー曲は自動除外、書き込み直前の自動バックアップ（`X:\LylicData\lyrics_backup`）。
4. **安全なリモートアクセス (Tailscale VPN)**:
   - Gradio Shareの脆弱性を排除し、ローカル起動（`0.0.0.0:7860`）＋ Tailscale暗号化メッシュVPNによるゼロトラスト運用。

---

## ディレクトリ構成

- `src/`: システム実装コード
  - `app.py`: WebUI（Gradioコンソール）
  - `batch_runner.py`: Pass 1〜3 自律バッチオーケストレーター
  - `critic.py`: 音楽評論家（Gemini）プロンプト・解説検証
  - `db.py`: SQLiteデータベース管理・マイグレーション
  - `itunes_writer.py`: iTunes COM書き込み・統合フォーマッタ
  - `lyrics_fetcher.py`: 多段階歌詞取得
  - `translator.py`: Ollama（Gemma 2 9B）ローカル和訳・VRAM保護
- `specs/`: 仕様書
  - `architecture_spec.md`: 正式仕様書 (v2.1)
  - `archive/`: 過去バージョン退避（トレーサビリティ）
- `tests/`: 自動テストスイート
- `TEST_EVIDENCE.md`: 検証エビデンス公式生ログ記録
- `USER_GUIDE.md`: ユーザーズガイド

---

## 起動方法

```bash
# 依存ライブラリのインストール
pip install -r requirements.txt # または必要なライブラリ

# WebUIの起動
run_app.bat
```
