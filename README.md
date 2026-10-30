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
