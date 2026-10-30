# moodle-mcp

東京工科大学の Moodle（学内ゲートつき）を AI から読むためのローカル MCP サーバー。
利用者の PC で動き、MCP クライアント（Claude Desktop / Claude Code / Antigravity CLI など）が stdio で起動する。

AI エージェントにこのリポジトリを渡して作業させる場合は [AGENTS.md](AGENTS.md) も読ませる。

## 使える人・環境

次をすべて満たす場合だけ動く。満たさない環境では導入しても Moodle を読めない。

- 東京工科大学（TEU）の Moodle アカウントを持っている
- 画面のある PC（macOS / Windows / Linux デスクトップ）で実行する。
  初回ログインで本人がブラウザを操作するため、サーバー・SSH・コンテナ・クラウドでは使えない
- PC で動く MCP クライアントを使う（下記「対応クライアント」）
- Python 3.11 以上、または [uv](https://docs.astral.sh/uv/getting-started/installation/)

Mac・Windows 実機での導入とログイン後の取得は未確認（[確認手順と記録](docs/verification.md)）。

## 方針

東京工科大学（TEU）の Moodle 専用。URL・日本語表示・学内ゲートを前提とし、
他大学の Moodle は対応範囲に含めない。`MOODLE_BASE_URL` による URL 変更は廃止した。

環境変数 `MOODLE_MCP_PROFILE_DIR` で専用プロファイルの保存先を変更できる。
`MOODLE_MCP_BROWSER_CHANNEL=chrome` または `msedge` でインストール済みの
ブラウザを使える（未指定なら Playwright 同梱 Chromium）。

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

## 導入（ソースから）

リポジトリを clone した利用者 PC で実行する。uv を使う場合:

```sh
git clone https://github.com/kadmalcun/moodle-mcp.git
cd moodle-mcp
uv sync                                   # .venv を作り、uv.lock どおりに依存を入れる
uv run playwright install chromium        # Chrome / Edge を使うなら省略可（後述）
uv run moodle login                       # 専用ブラウザでログインし、終わったらブラウザを終了する
uv run moodle deadlines                   # JSON が出れば成功
```

uv を使わない場合（Windows は `.venv\Scripts\` に読み替える）:

```sh
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install chromium
.venv/bin/moodle login
```

インストール済みの Chrome / Edge を使う場合は環境変数 `MOODLE_MCP_BROWSER_CHANNEL=chrome`（または `msedge`）を
指定し、`playwright install chromium` を省略できる。

MCP クライアントには `.venv` 内の `moodle-mcp` の絶対パスを stdio サーバーとして登録する
（Windows は `.venv\\Scripts\\moodle-mcp.exe`）。

```json
{"mcpServers":{"moodle":{"command":"/absolute/path/to/moodle-mcp/.venv/bin/moodle-mcp"}}}
```

登録後にクライアントを再起動し、ツール一覧に `moodle_deadlines` などが出ることを確認する。
SDK は [公式 Python SDK v1](https://py.sdk.modelcontextprotocol.io/v1/) を使用する。

## 手動ログイン

`moodle login`（MCP では `moodle_login`）で専用ブラウザが開く。
学内ゲート・Google・Moodle にログインし、Moodle の画面を確認してブラウザを終了する
（Mac はウィンドウを閉じるだけでは終わらないので ⌘Q）。
終了後に headless でログイン状態を確認する。パスワードはツールに渡さない。
ログイン操作には時間制限を設けていないため、MCP クライアントの待機時間にも注意する。
普段使いのブラウザでログイン済みでも、専用プロファイルでは初回ログインが必要。
同じプロファイルを複数の MCP/CLI プロセスから同時に使わない。

## MCP ツール

ツール: `moodle_login`、`moodle_deadlines`、`moodle_course`、
`moodle_announcements`、`moodle_activity`、`moodle_download_file`。
提出ツールは既定では公開しない（有効化は後述）。ダウンロードは保存先の絶対パスを指定し、既存ファイルは上書きしない。
ログイン要求が返ったら `moodle_login` を実行してから読み取りを再試行する。

## CLI

`uv run moodle ...`、`.venv/bin/moodle ...`、`python -m moodle_mcp ...` のいずれでも同じ。

```sh
moodle login
moodle deadlines --days-overdue 14
moodle course 'コース名の一部'
moodle announcements --days 30
moodle activity 123 --type assign
moodle download --cmid 456 --output /absolute/path/to/material.pdf
moodle serve
```

`login` はブラウザを閉じた後に認証を確認して終了する。
読み取りコマンドの成功結果は stdout の JSON、実行エラーは stderr の JSON。
終了コードは成功0、実行エラー1、引数エラー2、ログイン要求3、中断130。
`--help` と引数エラーは argparse の通常の表示。`serve` の stdout は MCP 通信専用。
CLI 終了時はブラウザを閉じる。MCP サーバーでは5分アイドルで閉じる。

## 提出ツール（任意）

`MOODLE_MCP_ENABLE_SUBMISSIONS=true` を設定して起動した場合だけ、
MCP の `moodle_submit_files` を公開する。CLI も同じ設定が必要。
MCP クライアントのサーバー設定では `env` にこの変数を追加して再起動する。

```sh
# macOS / Linux（Windows PowerShell は $env:MOODLE_MCP_ENABLE_SUBMISSIONS = 'true'）
export MOODLE_MCP_ENABLE_SUBMISSIONS=true
moodle submit 123 /absolute/path/to/report.pdf
# 下見の課題名、ファイル名、SHA-256、既存ファイルの置換計画と problems を確認する。
# 利用者が提出を了承した後にだけ --confirm を付けて再実行する。
```

MCP の引数は `cmid`、`paths`（ローカルパスの配列）、`confirm`（既定 false）。
下見ではアップロード・削除・保存を実行しない。実行前に対象とファイルを再確認する。
同名ファイルは置換し、それ以外の既存ファイルは残す。保存だけで提出になる課題がある。
ローカルでの読み込みは合計100 MiBまで。Moodle 側の個数・容量・形式の制約も下見で返す。
オンラインテキストの編集や、下書き保存後の提出確定操作には対応しない。

## 配布物からの導入

PyPI・公開リリースへの公開は未実施。wheel と .mcpb は `dist/` に生成されるが、
`dist/` はリポジトリに含めていない。clone しただけでは存在しないので、
先に「配布物の作成」の手順で作るか、作成者から受け取る。
ソースから使うだけなら上の「導入（ソースから）」で足りる。

### uvx（Claude Code 等）

[uv](https://docs.astral.sh/uv/getting-started/installation/) を先にインストールする。
wheel のパスには利用者 PC にコピーした実際のパスを指定する。

```sh
uvx --from /absolute/path/moodle_mcp-0.1.0-py3-none-any.whl moodle --help
uvx --from /absolute/path/moodle_mcp-0.1.0-py3-none-any.whl playwright install chromium
uvx --from /absolute/path/moodle_mcp-0.1.0-py3-none-any.whl moodle login
```

同梱 Chromium の代わりにインストール済み Chrome を使う場合は、環境変数
`MOODLE_MCP_BROWSER_CHANNEL=chrome` を指定する。この場合は Chromium の導入を省略できる。
Windows PowerShell では `$env:MOODLE_MCP_BROWSER_CHANNEL = 'chrome'` と指定する。

stdio クライアントの設定例（Windows はパスの `\` を `\\` にするか `/` を使う）:

```json
{
  "mcpServers": {
    "moodle": {
      "command": "uvx",
      "args": ["--from", "/absolute/path/moodle_mcp-0.1.0-py3-none-any.whl", "moodle-mcp"]
    }
  }
}
```

GUI から uvx が見つからない場合は `command` に uvx の絶対パスを指定する。
PyPI に公開した後は `uvx --from moodle-mcp==0.1.0 moodle-mcp` に置き換えられるが、現時点では使わない。

### .mcpb（Claude Desktop）

`dist/moodle-mcp-0.1.0.mcpb` を利用者 PC で開き、拡張機能としてインストールする。
[公式 MCPB の uv ランタイム仕様](https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md#uv-runtime-v04)
に対応したホストが必要。依存関係の初回導入にはネットワークを使う。

この配布形式は既定でインストール済み Google Chrome を使う。Edge を使う場合は
設定の「ブラウザ」を `msedge` にする。MCPB 自体にはブラウザ・ログイン情報を同梱しない。
「課題提出を有効にする」は既定でオフ。導入後に `moodle_login` を呼び、本人がログインする。
実機での導入・認証・読み取りは [確認手順](docs/verification.md) に沿って確認する。

## 開発

```sh
uv sync
uv run python -m unittest discover -s tests   # Moodle には接続しない。画面・ログインも不要
```

テストは標準の `unittest` で、Moodle とブラウザはモックする。追加の依存は要らない。
コードの構成と変更時のルールは [AGENTS.md](AGENTS.md) にまとめている。

## 配布物の作成（開発者向け）

```sh
uv lock --check
uv build                       # dist/ に wheel・sdist
uv run python scripts/build_mcpb.py
npx --yes @anthropic-ai/mcpb@2.1.2 validate manifest.json
npx --yes @anthropic-ai/mcpb@2.1.2 info dist/moodle-mcp-0.1.0.mcpb
```

wheel・sdist と、許可したソースファイルだけを含む .mcpb を `dist/` に出力する。
`.venv`、ブラウザのプロファイル、Cookie は含めない。MCPB の依存バージョンは
`uv.lock` に固定する。版を上げるときは `pyproject.toml` と `manifest.json` の `version` をそろえる。

## ライセンス

[MIT](LICENSE)
