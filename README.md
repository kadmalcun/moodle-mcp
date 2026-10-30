# moodle-mcp

東京工科大学の Moodle（学内ゲートつき）を AI から読むためのローカル MCP サーバー。
利用者の PC で動き、MCP クライアント（Claude Desktop / Claude Code / Antigravity CLI など）が stdio で起動する。
親タスク: vault の `01-Projects/Moodle MCPのローカル版.md`

自分用のリモート版（サーバー常駐、claude.ai・スマホから使う）は `~/dev/mcp-server`。取得処理はそこの `moodle_browser.py` が元。

## 方針

- トランスポート: stdio。認証・アップロード URL・ダウンロード URL は持たない（ローカルのパスを直接読み書きする）
- ブラウザ: Playwright の `launch_persistent_context` で専用プロファイルの Chromium を起動する
  - 学内ゲートのチケットは接続元に紐づくため、利用者の PC 上のブラウザで本人がログインする
  - 初回とログイン切れのときだけウィンドウを出して手でログイン。以降は headless
  - 普段使いの Chrome のプロファイルには CDP でつなげない（Chrome 側の制限）
- Moodle とのやりとりはすべてブラウザのページ内で行う（`page.evaluate` の `fetch`・DOM 読み取り）。
  古い TLS を Node 側が拒否するのと、ゲートの Cookie と `sesskey` が要るため
- 取得処理はトランスポートから切り離し、CLI（`moodle deadlines` など）からも呼べるようにする。
  MCP 非対応でもシェルが使えるエージェントや人が使える
- 書き込み（課題提出）は既定で無効。設定で有効にする

## 対応クライアント（2026-10 時点）

- 使える: Claude Desktop、Claude Code、Antigravity CLI（`~/.gemini/config/mcp_config.json`）など PC で動くもの
- 使えない: claude.ai・Gemini などの Web 版とスマホアプリ（クラウドから接続するためローカルに届かない）

## 手動ログインの確認（画面のある利用者 PC）

`playwright install chromium` で Chromium を用意する。専用ブラウザを開いて
学内ゲート・Google・Moodle にログインし、Moodle の画面を確認してウィンドウをすべて閉じる。
閉じた後に headless でログイン状態を確認する。パスワードはツールに渡さない。
ログイン操作には時間制限を設けていないため、MCP クライアントの待機時間にも注意する。

確認項目: 初回ログイン、再起動後の読み取り、ログアウト後のログイン要求、
Google のログイン可否、読み取り中のログイン拒否。同じプロファイルを複数の
MCP/CLI プロセスから同時に使わない。実機での確認は未実施。

## MCP の起動

開発時は `.venv/bin/pip install -e .` 後に `.venv/bin/moodle-mcp` で起動する。
MCP クライアントには、このコマンドの絶対パスを stdio サーバーとして登録する。
SDK は [公式 Python SDK v1](https://py.sdk.modelcontextprotocol.io/v1/) を使用する。

```json
{"mcpServers":{"moodle":{"command":"/absolute/path/to/.venv/bin/moodle-mcp"}}}
```

ツール: `moodle_login`、`moodle_deadlines`、`moodle_course`、
`moodle_announcements`、`moodle_activity`、`moodle_download_file`。
提出ツールは公開していない。ダウンロードは保存先の絶対パスを指定し、既存ファイルは上書きしない。
ログイン要求が返ったら `moodle_login` を実行してから読み取りを再試行する。

## CLI

開発用環境を再インストールした後、次のように実行する。
`python -m moodle_mcp` でも同じコマンドを使える。

```sh
.venv/bin/moodle login
.venv/bin/moodle deadlines --days-overdue 14
.venv/bin/moodle course 'コース名の一部'
.venv/bin/moodle announcements --days 30
.venv/bin/moodle activity 123 --type assign
.venv/bin/moodle download --cmid 456 --output /absolute/path/to/material.pdf
.venv/bin/moodle serve
```

`login` はブラウザを閉じた後に認証を確認して終了する。
読み取りコマンドの成功結果は stdout の JSON、実行エラーは stderr の JSON。
終了コードは成功0、実行エラー1、引数エラー2、ログイン要求3、中断130。
`--help` と引数エラーは argparse の通常の表示。`serve` の stdout は MCP 通信専用。
CLI 終了時はブラウザを閉じる。MCP サーバーでは5分アイドルで閉じる。
