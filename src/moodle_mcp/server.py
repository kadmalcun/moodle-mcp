"""既定で読み取り専用の stdio MCP サーバー。"""
from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from . import browser
from .operations import download_file, submit_files
from .config import submissions_enabled


@asynccontextmanager
async def lifespan(server):
    try:
        yield {}
    finally:
        await browser.close()


mcp = FastMCP('moodle-mcp', lifespan=lifespan)
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
async def moodle_login() -> dict:
    """利用者 PC にログイン用ウィンドウを開く。本人がログインしてブラウザを終了するまで待つ。
    Mac ではウィンドウを閉じるだけではブラウザが終わらないので、⌘Q で終了するよう利用者に伝える。"""
    return await browser.login()


@mcp.tool(annotations=READ)
async def moodle_deadlines(days_overdue: int = 14) -> dict:
    """締切一覧と提出状況。days_overdue は過去何日まで含めるか。"""
    if days_overdue < 0:
        raise ValueError('days_overdue は0以上にしてください')
    return await browser.list_deadlines(days_overdue)


@mcp.tool(annotations=READ)
async def moodle_course(query: str) -> dict:
    """コース名で検索し、教材・活動一覧を読む。"""
    return await browser.get_course(query)


@mcp.tool(annotations=READ)
async def moodle_announcements(days: int = 30) -> dict:
    """直近 days 日のお知らせを読む。"""
    if days < 0:
        raise ValueError('days は0以上にしてください')
    return await browser.list_announcements(days)


@mcp.tool(annotations=READ)
async def moodle_activity(cmid: int, modtype: str) -> dict:
    """活動の本文・添付・提出状況を読む。modtype は assign/resource/quiz 等。"""
    return await browser.get_activity(cmid, modtype)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
async def moodle_download_file(output: str, cmid: int | None = None, url: str | None = None) -> dict:
    """教材をローカルの output パスに保存。cmid または Moodle ファイル url の一方が必要。上書きしない。"""
    return await download_file(output, cmid, url)


if submissions_enabled():
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    async def moodle_submit_files(cmid: int, paths: list[str], confirm: bool = False) -> dict:
        """ローカルファイルを課題に提出。既定 confirm=false で課題・ファイル名・ハッシュ・置換計画を下見。
        下見結果を利用者に示し、提出の明示的な了承を得てから confirm=true で再実行すること。
        保存だけで提出済みになる課題がある。同名ファイルは置換される。
        """
        return await submit_files(cmid, paths, confirm)


def main():
    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
