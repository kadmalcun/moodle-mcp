"""MCP 非対応のエージェント・利用者向け CLI。"""
import argparse
import asyncio
import json
import sys
from . import browser
from .operations import download_file, submit_files


def nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError('0以上を指定してください')
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog='moodle', description='ローカル Chromium で Moodle を読む')
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('login', help='手動ログイン用ブラウザを開き、閉じるまで待つ')
    commands.add_parser('serve', help='stdio MCP サーバーを起動')
    deadlines = commands.add_parser('deadlines', help='締切と提出状況')
    deadlines.add_argument('--days-overdue', type=nonnegative, default=14)
    course = commands.add_parser('course', help='コースの教材・活動一覧')
    course.add_argument('query')
    announcements = commands.add_parser('announcements', help='直近のお知らせ')
    announcements.add_argument('--days', type=nonnegative, default=30)
    activity = commands.add_parser('activity', help='活動の本文・添付・提出状況')
    activity.add_argument('cmid', type=int)
    activity.add_argument('--type', dest='modtype', required=True, help='assign / resource / quiz 等')
    download = commands.add_parser('download', help='教材をローカル保存（上書き不可）')
    source = download.add_mutually_exclusive_group(required=True)
    source.add_argument('--cmid', type=int)
    source.add_argument('--url')
    download.add_argument('--output', required=True)
    submit = commands.add_parser('submit', help='課題のファイル提出（既定は下見、要設定）')
    submit.add_argument('cmid', type=int)
    submit.add_argument('paths', nargs='+')
    submit.add_argument('--confirm', action='store_true', help='下見内容を確認・了承した後に実際に提出する')
    return result


async def run(args: argparse.Namespace) -> dict:
    try:
        match args.command:
            case 'login':
                return await browser.login()
            case 'deadlines':
                return await browser.list_deadlines(args.days_overdue)
            case 'course':
                return await browser.get_course(args.query)
            case 'announcements':
                return await browser.list_announcements(args.days)
            case 'activity':
                return await browser.get_activity(args.cmid, args.modtype)
            case 'submit':
                return await submit_files(args.cmid, args.paths, args.confirm)
            case 'download':
                return await download_file(args.output, args.cmid, args.url)
        raise ValueError('未知のコマンドです')
    finally:
        await browser.close()


def main() -> int:
    args = parser().parse_args()
    if args.command == 'serve':
        from .server import main as serve
        serve()
        return 0
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(json.dumps({'error': type(error).__name__, 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 3 if isinstance(error, browser.MoodleLoginRequired) else 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
