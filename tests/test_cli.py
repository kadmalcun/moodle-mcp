import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from moodle_mcp import browser, cli
from moodle_mcp.operations import download_file


class CliTests(unittest.TestCase):
    def test_login_required_exit_and_streams(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('sys.argv', ['moodle', 'deadlines']), \
             patch.object(browser, 'list_deadlines', AsyncMock(side_effect=browser.MoodleLoginRequired('login'))), \
             patch.object(browser, 'close', AsyncMock()) as close, \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(), 3)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(json.loads(stderr.getvalue())['error'], 'MoodleLoginRequired')
        close.assert_awaited_once()

    def test_success_json_and_cleanup(self):
        stdout = io.StringIO()
        with patch('sys.argv', ['moodle', 'course', '日本語']), \
             patch.object(browser, 'get_course', AsyncMock(return_value={'name': '日本語'})) as course, \
             patch.object(browser, 'close', AsyncMock()) as close, \
             contextlib.redirect_stdout(stdout):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(json.loads(stdout.getvalue()), {'name': '日本語'})
        course.assert_awaited_once_with('日本語')
        close.assert_awaited_once()

    def test_download_requires_exactly_one_source(self):
        for extra in ([], ['--cmid', '1', '--url', 'https://example.com']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.parser().parse_args(['download', '--output', '/tmp/test', *extra])
            self.assertEqual(error.exception.code, 2)


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_binary_save_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'file.pdf'
            with patch.object(browser, 'fetch_file', AsyncMock(return_value={
                'name': 'original.pdf', 'content_type': 'application/pdf', 'body': b'\x00\xffPDF'
            })) as fetch:
                result = await download_file(str(path), cmid=1)
                self.assertEqual(path.read_bytes(), b'\x00\xffPDF')
                self.assertEqual(result['size'], 5)
                with self.assertRaises(FileExistsError):
                    await download_file(str(path), cmid=1)
                fetch.assert_awaited_once_with(cmid=1, url=None)
