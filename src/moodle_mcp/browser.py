"""利用者の PC 上の Chromium（専用プロファイル）で Moodle を読む

- 学内ゲートのチケットは接続元に紐づくため、Cookie を抜き出して httpx で叩く方式は使えない。
  ログイン状態は専用プロファイルの Chromium に持たせ、Playwright の launch_persistent_context で起動して使う
- Moodle 3.9 は Web Service が無効。AJAX（lib/ajax/service.php）で使えるのはタイムラインとコース一覧だけなので、
  課題の本文・添付・提出状況は課題ページ（mod/assign/view.php）の HTML から取る
- 書き込みは save_submission（課題のファイル提出）だけ。小テストの受験・出席登録はしない

取得処理はリモート版（~/dev/mcp-server の moodle_browser.py）とほぼ同じ。違うのはブラウザの用意だけ。
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from markdownify import markdownify
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from pypdf import PdfReader

# TEU 専用。URL だけ変えても他大学の認証・画面構成には対応できない。
BASE_URL = "https://service.cloud.teu.ac.jp/moodle_epyc"
# 既定は Playwright 同梱の Chromium（playwright install chromium が要る）。
# "chrome" / "msedge" にすると PC に入っているものを使う
BROWSER_CHANNEL = os.environ.get("MOODLE_MCP_BROWSER_CHANNEL") or None
# 最後の利用からこの秒数たったらブラウザを閉じる（MCP サーバーは長く生きるので、開きっぱなしにしない）
BROWSER_IDLE_SECONDS = 300
# moodle_download_file で渡すファイルの上限
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024

TZ = ZoneInfo("Asia/Tokyo")
NAV_TIMEOUT_MS = 45_000
PAGE_CONCURRENCY = 4
# 添付・本文のテキストはコンテキストを食うので上限を切る
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
# zip 内の1ファイルあたり。課題に関係ない同梱ファイル（Bean 等）で予算を使い切らないように
MAX_ENTRY_CHARS = 6_000


class MoodleLoginRequired(Exception):
    """ゲートか Google でログインを求められている。利用者がブラウザのウィンドウでログインし直す必要がある"""


def profile_dir() -> Path:
    """専用プロファイルの置き場所。普段使いの Chrome のプロファイルとは別にする"""
    if d := os.environ.get("MOODLE_MCP_PROFILE_DIR"):
        return Path(d).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "moodle-mcp" / "profile"


# プロセスの中で1つのブラウザを使い回す。同じプロファイルは同時に1つのブラウザしか開けないため、
# 並行して呼ばれたツールも同じコンテキストにタブを足して使う
_lock = asyncio.Lock()
_playwright: Playwright | None = None
_context: BrowserContext | None = None
_user_agent: str | None = None
_users = 0
_logging_in = False
_idle_close: asyncio.TimerHandle | None = None


async def _launch() -> BrowserContext:
    global _playwright, _context, _user_agent
    profile = profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        profile.chmod(0o700)  # ログイン済みの Cookie が入る
    _playwright = await async_playwright().start()
    try:
        _context = await _playwright.chromium.launch_persistent_context(
            profile,
            channel=BROWSER_CHANNEL,
            headless=True,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1280, "height": 900},
            # 自動操作の印を消す（Google のログインやゲートに弾かれないように）
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
        )
    except BaseException:
        await _playwright.stop()
        _playwright = None
        raise
    # headless の UA は "HeadlessChrome/…" になるので、タブごとに "Chrome/…" に差し替える。
    # 起動前には版が分からないので、起動後に実際の UA から作る
    page = _context.pages[0] if _context.pages else await _context.new_page()
    _user_agent = (await page.evaluate("() => navigator.userAgent")).replace("HeadlessChrome", "Chrome")
    _context.on("close", lambda _: _forget())
    return _context


def _forget() -> None:
    """ブラウザが落ちた・閉じられたとき、次の呼び出しで起動し直すようにする"""
    global _context
    _context = None


async def _close_locked() -> None:
    global _playwright, _context, _idle_close
    if _idle_close:
        _idle_close.cancel()
        _idle_close = None
    if _context:
        context, _context = _context, None
        await context.close()
    if _playwright:
        playwright, _playwright = _playwright, None
        await playwright.stop()


async def close() -> None:
    """ブラウザを閉じる。CLI は終わる前に呼ぶ。MCP サーバーはアイドルで自動的に閉じる"""
    async with _lock:
        await _close_locked()


async def _close_if_idle() -> None:
    async with _lock:
        # タイマーが切れてからロックを取るまでの間に使われ始めていたら閉じない
        if _users == 0:
            await _close_locked()


@asynccontextmanager
async def moodle_context():
    """専用プロファイルのブラウザ（headless）のコンテキスト。無ければ起動し、使い終わってしばらくしたら閉じる"""
    global _users, _idle_close
    async with _lock:
        if _idle_close:
            _idle_close.cancel()
            _idle_close = None
        if _logging_in:
            raise RuntimeError("手動ログイン中です。ブラウザを閉じてから再試行してください")
        context = _context or await _launch()
        _users += 1
    try:
        yield context
    finally:
        _users -= 1
        if _users == 0:
            _idle_close = asyncio.get_running_loop().call_later(
                BROWSER_IDLE_SECONDS, lambda: asyncio.ensure_future(_close_if_idle()))


@asynccontextmanager
async def new_tab(context: BrowserContext):
    page = await context.new_page()
    page.set_default_timeout(NAV_TIMEOUT_MS)
    try:
        if _user_agent:
            cdp = await context.new_cdp_session(page)
            await cdp.send("Emulation.setUserAgentOverride", {"userAgent": _user_agent, "acceptLanguage": "ja"})
        yield page
    finally:
        await page.close()


def _channel_executable(channel: str) -> str:
    """インストール済み Chrome / Edge の実行ファイル。Playwright と同じ標準の置き場所から探す。
    起動して Browser.getBrowserCommandLine で聞く方法は --enable-automation が無いと答えないので使わない
    """
    if sys.platform == "win32":
        exe = {"chrome": r"Google\Chrome\Application\chrome.exe",
               "msedge": r"Microsoft\Edge\Application\msedge.exe"}.get(channel)
        roots = [os.environ.get(k) for k in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")]
        candidates = [str(Path(r) / exe) for r in roots if r and exe]
    elif sys.platform == "darwin":
        app = {"chrome": "Google Chrome", "msedge": "Microsoft Edge"}.get(channel)
        candidates = [f"/Applications/{app}.app/Contents/MacOS/{app}",
                      str(Path.home() / f"Applications/{app}.app/Contents/MacOS/{app}")] if app else []
    else:
        candidates = {"chrome": ["/opt/google/chrome/chrome"], "msedge": ["/opt/microsoft/msedge/msedge"]}.get(channel, [])
    for c in candidates:
        if Path(c).is_file():
            return c
    raise RuntimeError(
        f"MOODLE_MCP_BROWSER_CHANNEL={channel} のブラウザが見つかりません（探した場所: {', '.join(candidates) or 'なし'}）"
    )


async def login() -> dict:
    """専用ブラウザを通常のプロセスとして開く。利用者が閉じるまで待つ。"""
    global _logging_in
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise RuntimeError("画面がありません。利用者の PC のデスクトップ上で login を実行してください")
    async with _lock:
        if _users or _logging_in:
            raise RuntimeError("ブラウザ使用中です。処理が終わってからログインしてください")
        await _close_locked()
        _logging_in = True
    try:
        profile = profile_dir().resolve()
        profile.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            profile.chmod(0o700)
        async with async_playwright() as pw:
            executable = _channel_executable(BROWSER_CHANNEL) if BROWSER_CHANNEL else pw.chromium.executable_path
            process = await asyncio.create_subprocess_exec(
                executable, f"--user-data-dir={profile}", "--no-first-run",
                "--no-default-browser-check",
                # Playwright（headless 側）が既定で付けるのと同じ Cookie の暗号鍵にする。
                # 付けないと Mac ではキーチェーンの鍵で暗号化され、headless 側で復号できずに未ログイン扱いになる
                "--use-mock-keychain", "--password-store=basic",
                f"{BASE_URL}/my/",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                code = await process.wait()
            finally:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
            if code:
                raise RuntimeError(f"ログイン用ブラウザが終了コード {code} で終了しました")
    finally:
        async with _lock:
            _logging_in = False
    try:
        async with moodle_context() as context, new_tab(context) as page:
            await goto(page, f"{BASE_URL}/my/")
        return {"logged_in": True}
    finally:
        await close()


async def goto(page: Page, url: str) -> None:
    """自動リダイレクトを待つ。ログイン画面なら45秒待たずに返す。"""
    await page.goto(url, wait_until="load")
    await page.wait_for_function(
        """base => {
          const here = location.origin + location.pathname;
          const moodle = here === base || here.startsWith(base + '/');
          return (moodle && window.M?.cfg?.sesskey)
            || location.hostname === 'accounts.google.com'
            || document.querySelector('input[type="password"]')
            || document.body.id === 'page-login-index';
        }""", arg=BASE_URL, timeout=NAV_TIMEOUT_MS,
    )
    logged_in = (page.url.split("?", 1)[0] == BASE_URL or page.url.startswith(BASE_URL + "/")) and await page.evaluate(
        "() => !!window.M?.cfg?.sesskey && document.body.id !== 'page-login-index'"
    )
    if not logged_in:
        u = urlsplit(page.url)
        raise MoodleLoginRequired(
            f"{u.netloc}{u.path}: moodle login（MCP では moodle_login）でログインしてください"
        )


async def ajax(page: Page, method: str, args: dict):
    """lib/ajax/service.php を呼ぶ。page はログイン済みの Moodle ページであること"""
    res = await page.evaluate(
        """async ([method, args]) => {
          const r = await fetch(`${M.cfg.wwwroot}/lib/ajax/service.php?sesskey=${M.cfg.sesskey}&info=${method}`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify([{index: 0, methodname: method, args}])});
          return (await r.json())[0];
        }""",
        [method, args],
    )
    if res.get("error"):
        raise RuntimeError(f"{method}: {res['exception'].get('errorcode')}: {res['exception'].get('message')}")
    return res["data"]


def _tidy(text: str) -> str:
    """改行を LF にそろえ、行末の空白と3行以上の空行を詰める（CRLF や &nbsp; はトークンの無駄）"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n").rstrip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（省略: 残り {len(text) - limit} 文字）"


# ひらがな・読点で終わる段落と、文字で始まる次の段落の間の空行
_MID_SENTENCE_BREAK = re.compile(r"(?<=[\u3041-\u309f、，,])\n\n(?=[\u3041-\u30ff\u4e00-\u9fffA-Za-z])")


def _html_to_md(html: str) -> str:
    """本文 HTML を Markdown にする。読む側は LLM なので、見た目より素直なテキストを優先する
    - * と _ はエスケープしない（`JAVA\\_TOOL\\_OPTIONS` のようになるため）
    - <br> の行末2スペースは _tidy で落とし、ただの改行にする
    - 先生が文の途中で <p> を分けている（「…配下に</p><p>置いて…」）ので、そこはつなぐ
    """
    text = markdownify(html, heading_style="ATX", escape_asterisks=False, escape_underscores=False)
    return _MID_SENTENCE_BREAK.sub("", _tidy(text))


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec="minutes")


def _cmid(url: str) -> int:
    return int(re.search(r"[?&]id=(\d+)", url).group(1))


# 課題ページの「提出ステータス」表を {見出し: 値} で返す
_JS_STATUS = """() => Object.fromEntries([...document.querySelectorAll('.submissionstatustable tr')]
  .map(r => [r.querySelector('th, td.c0')?.innerText.trim(), r.querySelector('td.lastcol, td.c1')?.innerText.trim()])
  .filter(([k, v]) => k && v !== undefined))"""

# タイムラインのイベント名から活動名を取り出す（assign / feedback / quiz の書式）
_TITLE_PATTERNS = [
    re.compile(r"^「(.*)」の提出期限$"),
    re.compile(r"^「(.*)」終了$"),
    re.compile(r"^(.*?)\s*の受験可能期間の終了$"),
]


def _title(name: str) -> str:
    for pat in _TITLE_PATTERNS:
        if m := pat.match(name):
            return m.group(1).strip()
    return name


_JP_DATETIME = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日(?:\s*\([^)]*\))?\s*(\d{1,2}):(\d{2})")


def _parse_jp_datetime(s: str | None) -> str | None:
    """「2026年 10月 26日(月曜日) 18:10」→ ISO 8601（JST）"""
    m = _JP_DATETIME.search(s or "")
    if not m:
        return None
    return datetime(*map(int, m.groups()), tzinfo=TZ).isoformat(timespec="minutes")


async def _read_status(page: Page) -> dict:
    """提出ステータス表。値の中の空白だけの行（"\\n \\n\\t\\n" の塊）を落とし、提出コメントは除く"""
    status = await page.evaluate(_JS_STATUS)
    status.pop("提出コメント", None)
    return {k: "\n".join(line.strip() for line in v.splitlines() if line.strip()) for k, v in status.items()}


def _submission(status: dict) -> dict:
    """提出ステータス表（日本語）から機械処理しやすい形を作る。元の表は status に残す"""
    files = []
    for line in (status.get("ファイル提出") or "").splitlines():
        if (ts := _parse_jp_datetime(line)) and files and "modified" not in files[-1]:
            files[-1]["modified"] = ts
        else:
            files.append({"name": line})
    return {
        "submitted": "提出済み" in (status.get("提出ステータス") or ""),
        "due": _parse_jp_datetime(status.get("終了日時") or status.get("提出期限")),
        "last_modified": _parse_jp_datetime(status.get("最終更新日時")),
        "files": files,
    }


async def _submission_status(context: BrowserContext, sem: asyncio.Semaphore, cmid: int) -> dict:
    async with sem, new_tab(context) as page:
        await goto(page, f"{BASE_URL}/mod/assign/view.php?id={cmid}")
        return await _read_status(page)


async def list_deadlines(days_overdue: int = 14) -> dict:
    """タイムライン（締切つきで、まだ対応が要るもの）を返す。課題には各課題ページの提出ステータスを付ける"""
    now = int(datetime.now(TZ).timestamp())
    async with moodle_context() as context:
        async with new_tab(context) as page:
            await goto(page, f"{BASE_URL}/my/")
            data = await ajax(page, "core_calendar_get_action_events_by_timesort", {
                "timesortfrom": now - days_overdue * 86400,
                "limitnum": 50,
            })
        events = [e for e in data["events"] if e.get("url") and "id=" in e["url"]]
        assigns = [e for e in events if e["modulename"] == "assign"]
        sem = asyncio.Semaphore(PAGE_CONCURRENCY)
        statuses = await asyncio.gather(
            *(_submission_status(context, sem, _cmid(e["url"])) for e in assigns)
        )
    status_by_id = {_cmid(e["url"]): st.get("提出ステータス") for e, st in zip(assigns, statuses)}
    items = []
    for e in events:
        item = {
            "id": _cmid(e["url"]),
            "type": e["modulename"],
            "course": e["course"]["fullname"],
            "title": _title(e["name"]),
            "due": _iso(e["timesort"]),
            "overdue": e["timesort"] < now,
        }
        if item["id"] in status_by_id:
            item["status"] = status_by_id[item["id"]]
            item["submitted"] = "提出済み" in (item["status"] or "")
        items.append(item)
    items.sort(key=lambda a: a["due"])
    return {"fetched_at": _iso(now), "deadlines": items}


async def _find_course(page: Page, query: str) -> dict:
    """コース ID か、コース名の一部からコースを1つ決める。履修中のコースを優先する"""
    q = query.strip().lower()
    names = []
    for classification in ("inprogress", "all"):
        data = await ajax(page, "core_course_get_enrolled_courses_by_timeline_classification", {
            "classification": classification, "limit": 0, "offset": 0,
        })
        courses = data["courses"]
        if classification == "inprogress":
            names = [c["fullname"] for c in courses]
        hits = [c for c in courses if str(c["id"]) == q or q in c["fullname"].lower()]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ValueError(f"「{query}」に当てはまるコースが複数ある: " + " / ".join(c["fullname"] for c in hits))
    raise ValueError(f"「{query}」に当てはまるコースが無い。履修中: " + " / ".join(names))


async def get_course(query: str) -> dict:
    """コースの各セクション（授業回）と、そこに置かれた資料・活動の一覧"""
    async with moodle_context() as context, new_tab(context) as page:
        await goto(page, f"{BASE_URL}/my/")
        course = await _find_course(page, query)
        await goto(page, f"{BASE_URL}/course/view.php?id={course['id']}")
        sections = await page.evaluate("""() => [...document.querySelectorAll('li.section')].map(s => ({
          name: s.querySelector('.sectionname')?.innerText.trim(),
          summary: (s.querySelector('.summary')?.innerText.trim() || '').slice(0, 300),
          activities: [...s.querySelectorAll('li.activity')].map(a => {
            const type = ([...a.classList].find(c => c.startsWith('modtype_')) || '').replace('modtype_', '');
            const href = a.querySelector('a[href*="/mod/"]')?.href;
            const id = href && new URL(href).searchParams.get('id');
            return type === 'label'
              ? {type, text: a.innerText.trim().slice(0, 300)}
              : {id: id ? Number(id) : null, type, name: a.querySelector('.instancename')?.childNodes[0]?.textContent.trim()};
          }),
        }))""")
    return {
        "id": course["id"],
        "course": course["fullname"],
        "sections": [s for s in sections if s["activities"] or s["summary"]],
    }


async def _news_forums(context: BrowserContext, sem: asyncio.Semaphore, course: dict) -> list[dict]:
    """コースのアナウンスメント（forumtype-news）フォーラムの cmid を探す。セクション0にあるのが普通"""
    async with sem, new_tab(context) as page:
        await goto(page, f"{BASE_URL}/course/view.php?id={course['id']}")
        ids = await page.evaluate("""() => [...document.querySelectorAll('#section-0 li.activity.modtype_forum a[href*="/mod/forum/view.php"]')]
          .map(a => Number(new URL(a.href).searchParams.get('id')))""")
        forums = []
        for cmid in dict.fromkeys(ids):
            await goto(page, f"{BASE_URL}/mod/forum/view.php?id={cmid}")
            if not await page.evaluate("() => document.body.classList.contains('forumtype-news')"):
                continue
            rows = await page.evaluate("""() => [...document.querySelectorAll('tr.discussion')].map(r => ({
              subject: r.querySelector('.topic')?.innerText.trim(),
              author: r.querySelector('.author')?.innerText.trim().split('\\n')[0],
              ts: Number(r.querySelector('[data-timestamp]')?.dataset.timestamp || 0),
              url: r.querySelector('a[href*="discuss.php"]')?.href}))""")
            forums.append({"course": course["fullname"], "discussions": rows})
        return forums


async def _first_post(context: BrowserContext, sem: asyncio.Semaphore, url: str) -> dict:
    async with sem, new_tab(context) as page:
        await goto(page, url)
        return await page.evaluate("""() => {
          const p = document.querySelector('.forumpost.firstpost') || document.querySelector('.forumpost');
          return {
            html: p?.querySelector('.post-content-container')?.innerHTML || '',
            attachments: [...(p?.querySelectorAll('a[href*="pluginfile.php"]') || [])].map(a => a.innerText.trim()).filter(Boolean),
          };
        }""")


async def list_announcements(days: int = 30) -> dict:
    """履修中の全コースのアナウンスメントから、days 日以内の投稿を本文つきで返す（新しい順）"""
    now = int(datetime.now(TZ).timestamp())
    since = now - days * 86400
    async with moodle_context() as context:
        async with new_tab(context) as page:
            await goto(page, f"{BASE_URL}/my/")
            courses = (await ajax(page, "core_course_get_enrolled_courses_by_timeline_classification", {
                "classification": "inprogress", "limit": 0, "offset": 0,
            }))["courses"]
        sem = asyncio.Semaphore(PAGE_CONCURRENCY)
        per_course = await asyncio.gather(*(_news_forums(context, sem, c) for c in courses))
        items = [
            {"course": f["course"], **d}
            for forums in per_course for f in forums for d in f["discussions"]
            if d["url"] and d["ts"] >= since
        ]
        items.sort(key=lambda d: d["ts"], reverse=True)
        posts = await asyncio.gather(*(_first_post(context, sem, d["url"]) for d in items))

    budget = MAX_TEXT_CHARS
    announcements = []
    for d, p in zip(items, posts):
        # 1件が長すぎて他を押し出さないよう、1件あたりにも上限を切る
        text = _truncate(_html_to_md(p["html"]), min(budget, MAX_TEXT_CHARS // 4))
        budget = max(0, budget - len(text))
        item = {
            "course": d["course"],
            "subject": d["subject"],
            "author": d["author"],
            "posted": _iso(d["ts"]),
            "text": text,
            "url": d["url"],
        }
        if p["attachments"]:
            item["attachments"] = p["attachments"]
        announcements.append(item)
    return {"fetched_at": _iso(now), "announcements": announcements}


_TEXT_EXTENSIONS = (
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yml", ".yaml", ".properties", ".sql", ".sh",
    ".py", ".c", ".h", ".cpp", ".java", ".kt", ".js", ".ts", ".go", ".rb", ".html", ".css", ".jsp",
)


def _extract_text(name: str, content_type: str, body: bytes) -> str | None:
    lower = name.lower()
    if lower.endswith(".pdf") or "pdf" in content_type:
        reader = PdfReader(io.BytesIO(body))
        return _tidy("\n".join((p.extract_text() or "") for p in reader.pages))
    if lower.endswith(".zip") or "zip" in content_type:
        # 中のテキストファイルをつなげる。サンプルコードの配布でよく使われる
        parts = []
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            entries = [i for i in z.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")
                       and "/." not in "/" + i.filename]
            parts.append("ファイル一覧:\n" + "\n".join(f"{i.filename} ({i.file_size} B)" for i in entries))
            for i in entries:
                if i.filename.lower().endswith(_TEXT_EXTENSIONS):
                    text = _tidy(z.read(i).decode("utf-8", errors="replace"))
                    parts.append(f"=== {i.filename} ===\n" + _truncate(text, MAX_ENTRY_CHARS))
        return "\n\n".join(parts)
    if content_type.startswith("text/") or lower.endswith(_TEXT_EXTENSIONS):
        return _tidy(body.decode("utf-8", errors="replace"))
    return None


async def download(page: Page, url: str, max_bytes: int = MAX_ATTACHMENT_BYTES) -> dict:
    """ページ内の fetch でファイルを取る。
    Playwright の context.request は Node 側の OpenSSL を使い、Moodle の古い TLS（DH 鍵が短い）を拒否するため使えない
    """
    res = await page.evaluate(
        """async ([url, maxBytes]) => {
          const r = await fetch(url, {credentials: 'include'});
          const blob = await r.blob();
          const out = {status: r.status, ok: r.ok, type: r.headers.get('content-type') || '', size: blob.size,
                       name: decodeURIComponent(new URL(r.url).pathname.split('/').pop())};
          if (r.ok && blob.size <= maxBytes) {
            const bytes = new Uint8Array(await blob.arrayBuffer());
            let bin = '';
            for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
            out.b64 = btoa(bin);
          }
          return out;
        }""",
        [url, max_bytes],
    )
    res["body"] = base64.b64decode(res.pop("b64")) if "b64" in res else None
    return res


def _file_item(f: dict, name: str, budget: int) -> dict:
    """ダウンロード結果を {name, text | note} にする。text は budget 文字まで"""
    item = {"name": name}
    if not f["ok"]:
        item["note"] = f"ダウンロード失敗: HTTP {f['status']}"
    elif f["body"] is None:
        item["note"] = f"大きすぎるので省略（{f['size'] // 1024} KiB）"
    else:
        text = _extract_text(f["name"], f["type"], f["body"])
        if text is None:
            item["note"] = f"テキスト化できない形式（{f['type'] or f['name']}）"
        else:
            item["text"] = _truncate(text, budget)
            if len(text) > budget:
                item["note"] = f"長いので {budget} 文字で切った（全 {len(text)} 文字）"
    return item


_JS_HEADER = """() => ({
  title: document.querySelector('#region-main h2')?.innerText.trim() || document.title,
  course: document.querySelector('.page-header-headings h1')?.innerText.trim(),
})"""


async def _assignment(page: Page) -> dict:
    info = await page.evaluate("""() => {
      const intro = document.querySelector('#intro');
      const links = [...(intro?.querySelectorAll('a[href*="pluginfile.php"]') || [])];
      return {
        intro_html: intro?.querySelector('.no-overflow')?.innerHTML || '',
        files: [...new Map(links.map(a => [a.href.split('?')[0], a.innerText.trim()])).entries()],
      };
    }""")
    status = await _read_status(page)
    attachments = []
    budget = MAX_TEXT_CHARS
    for url, label in info["files"][:MAX_ATTACHMENTS]:
        f = await download(page, url)
        item = {**_file_item(f, label or f["name"], budget), "url": url}
        budget = max(0, budget - len(item.get("text", "")))
        attachments.append(item)
    for url, label in info["files"][MAX_ATTACHMENTS:]:
        attachments.append({"name": label, "url": url, "note": f"添付が {MAX_ATTACHMENTS} 個を超えたので中身は省略"})
    return {
        "intro": _html_to_md(info["intro_html"]),
        "status": status,
        "submission": _submission(status),
        "attachments": attachments,
    }


async def _fetch_resource(page: Page, url: str, max_bytes: int = MAX_ATTACHMENT_BYTES) -> dict | None:
    """resource の view.php からファイルを取る。page は resource 以外の Moodle ページを開いておく"""
    f = await download(page, url, max_bytes)
    if f["ok"] and "text/html" in f["type"]:
        # 表示方法が「埋め込み」等だと HTML ページが返るので、中のファイルへのリンクを辿る
        await goto(page, url)
        file_url = await page.evaluate("""() => {
          const el = document.querySelector('#region-main a[href*="pluginfile.php"], object[data*="pluginfile.php"], iframe[src*="pluginfile.php"]');
          return el && (el.href || el.data || el.src);
        }""")
        if not file_url:
            return None
        f = await download(page, file_url, max_bytes)
    return f


async def _resource(page: Page, url: str) -> dict:
    f = await _fetch_resource(page, url)
    if f is None:
        return {"note": "ファイルへのリンクが見つからない"}
    return {"file": _file_item(f, f["name"], MAX_TEXT_CHARS)}


async def _region_main(page: Page) -> dict:
    html = await page.evaluate("""() => {
      const main = document.querySelector('#region-main')?.cloneNode(true);
      if (!main) return '';
      main.querySelectorAll('script, style, form, nav, .activity-navigation, h2').forEach(e => e.remove());
      return main.innerHTML;
    }""")
    text = _html_to_md(html)
    out = {"content": _truncate(text, MAX_TEXT_CHARS)}
    if len(text) > MAX_TEXT_CHARS:
        out["note"] = f"長いので {MAX_TEXT_CHARS} 文字で切った（全 {len(text)} 文字）"
    return out


async def get_activity(cmid: int, modtype: str) -> dict:
    """活動1件の中身。assign は本文・提出状況・添付、resource はファイルのテキスト、
    url はリンク先、それ以外はページ本文を Markdown で返す"""
    if not re.fullmatch(r"[a-z]+", modtype):
        raise ValueError(f"type が不正: {modtype!r}")
    view = f"{BASE_URL}/mod/{modtype}/view.php?id={cmid}"
    async with moodle_context() as context, new_tab(context) as page:
        if modtype == "resource":
            # resource の view.php はファイルのダウンロードを始めるので開かない。
            # 別の Moodle ページから fetch する（ゲートの Cookie と sesskey が要るため）
            await goto(page, f"{BASE_URL}/my/")
            res = await _resource(page, view)
            return {"id": cmid, "type": modtype, "title": res.get("file", {}).get("name"), "url": view, **res}

        # url は既定で外部へリダイレクトするので、redirect=0 で Moodle 側のページに留める
        await goto(page, view + ("&redirect=0" if modtype == "url" else ""))
        result = {"id": cmid, "type": modtype, **await page.evaluate(_JS_HEADER), "url": view}
        if modtype == "assign":
            result |= await _assignment(page)
        elif modtype == "url":
            result["link"] = await page.evaluate(
                """(host) => document.querySelector('.urlworkaround a')?.href
                  || [...document.querySelectorAll('#region-main a[href^="http"]')]
                       .find(a => new URL(a.href).host !== host)?.href || null""",
                urlsplit(BASE_URL).netloc,
            )
            result |= await _region_main(page)
        else:
            result |= await _region_main(page)
    return result


# ---- 課題の提出（書き込み） ----
# この Moodle は保存ボタンが「この状態で提出する」で、保存＝提出になる課題が多いとみられる。
# そのため save_submission は confirm=False で下見だけ返し、確認後の confirm=True で初めて書き込む。

async def _open_submission_form(page: Page, cmid: int) -> dict:
    await goto(page, f"{BASE_URL}/mod/assign/view.php?id={cmid}&action=editsubmission")
    form = await page.evaluate("""() => {
      const f = document.querySelector('#region-main form.mform');
      if (!f) return null;
      return {
        itemid: f.querySelector('input[name=files_filemanager]')?.value || null,
        has_onlinetext: !!f.querySelector('[name="onlinetext_editor[text]"]'),
        save_label: f.querySelector('input[name=submitbutton]')?.value,
      };
    }""")
    if form is None:
        msg = await page.evaluate("() => document.querySelector('#region-main')?.innerText.trim().split('\\n')[0]")
        raise ValueError(f"この課題は今は提出・編集できない（受付前・締切後・権限なし等）: {msg}")
    if not form["itemid"]:
        raise ValueError("この課題はファイル提出の形式ではない")

    html = await page.content()
    init = html[html.find("M.form_filemanager.init(Y,"):][:20000]

    def field(key: str) -> str | None:
        m = re.search(rf'"{key}":("[^"]*"|\[[^\]]*\]|-?\d+)', init)
        return json.loads(m.group(1)) if m else None

    repo = re.search(r'"id":"?(\d+)"?,[^{}]*?"type":"upload"', init)
    ctx = re.search(r'"context":\{"id":(\d+)', init)
    if not (repo and ctx):
        raise RuntimeError("ファイルアップロードの設定が読み取れない")
    return {
        **form,
        "repo_id": repo.group(1),
        "ctx_id": ctx.group(1),
        "client_id": field("client_id") or "",
        "maxbytes": int(field("maxbytes") or 0),
        "maxfiles": int(field("maxfiles") or 0),
        "areamaxbytes": int(field("areamaxbytes") or -1),
        "accepted_types": field("accepted_types") or [],
        "author": field("author") or "",
        "license": field("defaultlicense") or "allrightsreserved",
    }


async def _draft_call(page: Page, action: str, params: dict) -> dict:
    return await page.evaluate(
        """async ([action, params]) => {
          const body = new URLSearchParams({sesskey: M.cfg.sesskey, filepath: '/', ...params});
          const r = await fetch(`${M.cfg.wwwroot}/repository/draftfiles_ajax.php?action=${action}`, {method: 'POST', body});
          return JSON.parse(await r.text() || 'null');
        }""",
        [action, params],
    )


async def _upload(page: Page, form: dict, name: str, body: bytes) -> dict:
    return await page.evaluate(
        """async ([b64, name, p]) => {
          const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
          const fd = new FormData();
          fd.append('repo_upload_file', new Blob([bytes]), name);
          for (const [k, v] of Object.entries({
            sesskey: M.cfg.sesskey, repo_id: p.repo_id, itemid: p.itemid, ctx_id: p.ctx_id, client_id: p.client_id,
            author: p.author, license: p.license, maxbytes: p.maxbytes, areamaxbytes: p.areamaxbytes,
            savepath: '/', title: name, env: 'filemanager'})) fd.append(k, v);
          for (const t of p.accepted_types) fd.append('accepted_types[]', t);
          const r = await fetch(`${M.cfg.wwwroot}/repository/repository_ajax.php?action=upload`, {method: 'POST', body: fd});
          return JSON.parse(await r.text());
        }""",
        [base64.b64encode(body).decode(), name, form],
    )


def _check_files(form: dict, existing: list[dict], files: list[tuple[str, bytes]]) -> list[str]:
    problems = []
    names = [n for n, _ in files]
    if len(set(names)) != len(names):
        problems.append("同じ名前のファイルが複数ある")
    for name, body in files:
        if not name or "/" in name or "\\" in name:
            problems.append(f"ファイル名が不正: {name!r}")
        if form["maxbytes"] > 0 and len(body) > form["maxbytes"]:
            problems.append(f"{name}: 大きすぎる（{len(body)} バイト、上限 {form['maxbytes']}）")
        types = [t.lower() for t in form["accepted_types"] if t != "*"]
        if types and not name.lower().endswith(tuple(types)):
            problems.append(f"{name}: 受け付けない形式（許可: {' '.join(types)}）")
    total = len({e["name"] for e in existing} | set(names))
    if form["maxfiles"] > 0 and total > form["maxfiles"]:
        problems.append(f"ファイル数が上限を超える（{total} 個、上限 {form['maxfiles']}）")
    return problems


async def save_submission(cmid: int, files: list[tuple[str, bytes]], confirm: bool) -> dict:
    """課題にファイルをアップロードして保存する。confirm=False なら何も変えずに下見だけ返す"""
    if confirm:
        from .config import require_submissions
        require_submissions()
    if not files and confirm:
        raise ValueError("ファイルが1つも指定されていない")
    async with moodle_context() as context, new_tab(context) as page:
        await goto(page, f"{BASE_URL}/mod/assign/view.php?id={cmid}")
        header = await page.evaluate(_JS_HEADER)
        status_before = await _read_status(page)

        form = await _open_submission_form(page, cmid)
        listing = await _draft_call(page, "list", {"itemid": form["itemid"], "client_id": form["client_id"]})
        existing = [{"name": f["filename"], "size": f.get("size")} for f in (listing or {}).get("list", [])
                    if f.get("type") != "folder"]
        existing_names = {e["name"] for e in existing}
        problems = _check_files(form, existing, files)
        plan = [{"name": n, "size": len(b), "sha256": hashlib.sha256(b).hexdigest(),
                 "replaces_existing": n in existing_names} for n, b in files]
        # 追記型: 下書き領域には既存ファイルが入っていて、同名のものだけ消してから上げる
        after_save = sorted(existing_names | {n for n, _ in files})
        base = {
            "id": cmid,
            "course": header["course"],
            "assignment": header["title"],
            "url": f"{BASE_URL}/mod/assign/view.php?id={cmid}",
        }

        if not confirm or problems:
            return {
                **base,
                "confirmed": False,
                "status": status_before,
                "submission": _submission(status_before),
                "existing_files": existing,
                "upload_plan": plan,
                "files_after_save": after_save,
                "problems": problems,
                "warning": (
                    f"保存ボタンは「{form['save_label']}」。この Moodle では保存した時点で提出済みになる課題がある"
                    "（締切前なら通常は編集し直せる）"
                ),
                "next": "問題なし。内容を利用者に確認してから confirm=true で呼ぶ" if not problems
                        else "problems を解消してから呼び直す",
            }

        for name, body in files:
            if name in existing_names:
                await _draft_call(page, "delete", {
                    "itemid": form["itemid"], "client_id": form["client_id"], "filename": name,
                })
            res = await _upload(page, form, name, body)
            if not res or res.get("error") or res.get("event") == "fileexists":
                raise RuntimeError(f"{name} のアップロードに失敗: {res}")

        async with page.expect_navigation(wait_until="load"):
            await page.click("#region-main form.mform input[name=submitbutton]")
        errors = await page.evaluate(
            "() => [...document.querySelectorAll('#region-main .error, #region-main .alert-danger')]"
            ".map(e => e.innerText.trim()).filter(Boolean)"
        )
        if errors:
            raise RuntimeError("保存でエラー: " + " / ".join(errors))

        await goto(page, base["url"])
        status_after = await _read_status(page)
        can_finalize = await page.evaluate(
            "() => !!document.querySelector('#region-main form input[name=action][value=submit]')"
        )
    return {
        **base,
        "confirmed": True,
        "uploaded": [n for n, _ in files],
        "status": status_after,
        "submission": _submission(status_after),
        "finalized": not can_finalize and "提出済み" in (status_after.get("提出ステータス") or ""),
        "note": "下書き保存。確定は Moodle の画面で「課題を提出する」を押す" if can_finalize
                else "保存＝提出の課題。締切前なら「提出を編集する」で差し替えられる",
    }


# ---- ファイルの取得（保存先は呼び出し側が決める） ----

def safe_filename(name: str) -> str:
    name = name.replace("/", "_").replace("\\", "_").strip().lstrip(".")
    if not name:
        raise ValueError("ファイル名が取れない")
    return name


async def fetch_file(cmid: int | None = None, url: str | None = None) -> dict:
    """配布ファイル（resource の id）か、Moodle 内のファイル URL（pluginfile.php）を取ってくる"""
    if (cmid is None) == (url is None):
        raise ValueError("id と url のどちらか一方を指定する")
    if url is not None and not url.startswith(f"{BASE_URL}/pluginfile.php/"):
        raise ValueError(f"url は {BASE_URL}/pluginfile.php/ で始まる Moodle のファイルに限る")
    async with moodle_context() as context, new_tab(context) as page:
        await goto(page, f"{BASE_URL}/my/")
        if cmid is not None:
            f = await _fetch_resource(page, f"{BASE_URL}/mod/resource/view.php?id={cmid}", MAX_DOWNLOAD_BYTES)
            if f is None:
                raise ValueError(f"id={cmid} からファイルが見つからない（resource の id か確認）")
        else:
            f = await download(page, url, MAX_DOWNLOAD_BYTES)
    if not f["ok"]:
        raise RuntimeError(f"ダウンロード失敗: HTTP {f['status']}")
    if f["body"] is None:
        raise ValueError(f"大きすぎる（{f['size'] // 1024 // 1024} MiB、上限 {MAX_DOWNLOAD_BYTES // 1024 // 1024} MiB）")
    return {"name": safe_filename(f["name"]), "content_type": f["type"], "body": f["body"]}

