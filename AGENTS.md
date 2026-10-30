# AGENTS.md

AI エージェントがこのリポジトリで作業するときの指示。利用者向けの説明は [README.md](README.md)。

## 概要

東京工科大学（TEU）の Moodle を、利用者 PC 上の専用プロファイルの Chromium（Playwright）経由で読む
ローカル MCP サーバーと CLI。Python 3.11 以上、依存は `uv.lock` で固定。

## 構成

| ファイル | 役割 |
| --- | --- |
| `src/moodle_mcp/browser.py` | ブラウザの起動・ログイン・Moodle からの取得と提出。処理の本体 |
| `src/moodle_mcp/operations.py` | MCP と CLI 共通のローカルファイル操作（保存・提出前の読み込み） |
| `src/moodle_mcp/server.py` | stdio MCP サーバー（FastMCP）。ツール定義だけを持つ |
| `src/moodle_mcp/cli.py` | `moodle` コマンド。結果は stdout に JSON、エラーは stderr に JSON |
| `src/moodle_mcp/config.py` | 提出ツールの有効化設定 |
| `bundle/server.py` | .mcpb のエントリポイント |
| `scripts/build_mcpb.py` | .mcpb の作成。含めるファイルを明示的に列挙している |
| `manifest.json` | .mcpb のマニフェスト |
| `docs/verification.md` | 利用者 PC での実機確認の手順と記録 |

Moodle 3.9 は Web Service が無効で、AJAX（`lib/ajax/service.php`）で取れるのはタイムラインとコース一覧だけ。
それ以外は各ページの HTML から読む。理由と詳細は `browser.py` 冒頭の docstring と README の「方針」。

## コマンド

```sh
uv sync                                        # 開発環境
uv run python -m unittest discover -s tests    # テスト（変更後は必ず実行）
uv run moodle --help                           # CLI
uv lock --check                                # 依存を変えたとき
```

## 守ること

- **TEU 専用を保つ。** URL（`BASE_URL`）を設定可能にする、他大学向けに汎用化する、といった変更はしない。
- **提出は既定で無効・既定で下見のまま。** `MOODLE_MCP_ENABLE_SUBMISSIONS` が `true` / `1` 以外なら提出ツールを公開しない。
  `confirm` の既定値を `true` にしない。下見ではアップロード・削除・保存をしない（`tests/test_submission.py` が確認している）。
- **書き込みは課題のファイル提出だけ。** 小テストの受験、出席登録、フォーラム投稿などの書き込みを追加しない。
- **認証情報を扱わない。** パスワードを受け取るツール、Cookie を書き出して別の HTTP クライアントで使う処理は作らない
  （学内ゲートのチケットが接続元に紐づくため、そもそも動かない）。Moodle との通信はページ内の `fetch` で行う。
- **上書きしない。** ダウンロードの保存先が既にあればエラーにする。
- **本物の Moodle に接続しない。** テストは `unittest.mock` で `browser` の関数をモックする。
  実機での確認は利用者本人が `docs/verification.md` の手順で行う。開発環境（画面なし）ではログインできない。
- **`serve` / MCP サーバーの stdout に書かない。** stdio の MCP 通信が壊れる。ログは stderr へ。
- **配布物に私的なファイルを入れない。** `.venv`、プロファイル、Cookie、`dist/` はコミットしない。
  .mcpb に入れるファイルを増やすときは `scripts/build_mcpb.py` の列挙に明示的に足す。
- **版をそろえる。** `pyproject.toml` と `manifest.json` の `version` は同じにする（`build_mcpb.py` が検査する）。
- **ツールを増減したら** `tests/test_server.py` のツール一覧と README の「MCP ツール」「CLI」を更新する。

## 書き方

- コメント・docstring・エラーメッセージ・ドキュメントは日本語。既存の文体（だ・である調、短い文）に合わせる。
- エラーメッセージは利用者が次に何をすればよいか分かるように書く（例: 提出が無効なら有効化の方法を示す）。
- 既存のコードの密度と命名に合わせ、不要な抽象化を足さない。
