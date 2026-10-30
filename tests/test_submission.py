import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch
from moodle_mcp import browser
from moodle_mcp.operations import submit_files
from moodle_mcp.config import submissions_enabled


class SubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_before_local_or_remote_access(self):
        with patch.dict(os.environ, {'MOODLE_MCP_ENABLE_SUBMISSIONS': 'false'}), \
             patch.object(browser, 'save_submission', AsyncMock()) as save:
            with self.assertRaises(PermissionError):
                await submit_files(1, ['/does/not/exist'])
            save.assert_not_awaited()

    async def test_unknown_config_disabled(self):
        with patch.dict(os.environ, {'MOODLE_MCP_ENABLE_SUBMISSIONS': 'yes'}):
            self.assertFalse(submissions_enabled())

    async def test_preview_passes_binary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'レポート.pdf'
            path.write_bytes(b'\x00\xffPDF')
            with patch.dict(os.environ, {'MOODLE_MCP_ENABLE_SUBMISSIONS': 'true'}), \
                 patch.object(browser, 'save_submission', AsyncMock(return_value={'confirmed': False})) as save:
                self.assertEqual(await submit_files(42, [str(path)]), {'confirmed': False})
                save.assert_awaited_once_with(42, [('レポート.pdf', b'\x00\xffPDF')], confirm=False)
                with self.assertRaisesRegex(ValueError, '重複'):
                    await submit_files(42, [str(path), str(path)])
                self.assertEqual(save.await_count, 1)

    async def test_browser_preview_never_uploads_deletes_or_saves(self):
        page = AsyncMock()
        page.evaluate.return_value = {'course': 'course', 'title': 'assignment'}

        @asynccontextmanager
        async def context(*args):
            yield page

        form = {'itemid': '1', 'client_id': 'client', 'maxbytes': 1000,
                'maxfiles': 5, 'accepted_types': [], 'save_label': 'この状態で提出する'}
        with patch.object(browser, 'moodle_context', context), \
             patch.object(browser, 'new_tab', context), \
             patch.object(browser, 'goto', AsyncMock()), \
             patch.object(browser, '_read_status', AsyncMock(return_value={})), \
             patch.object(browser, '_open_submission_form', AsyncMock(return_value=form)), \
             patch.object(browser, '_draft_call', AsyncMock(return_value={'list': [{'filename': 'report.pdf', 'size': 3}]})) as draft, \
             patch.object(browser, '_upload', AsyncMock()) as upload:
            result = await browser.save_submission(42, [('report.pdf', b'new')], confirm=False)
        self.assertFalse(result['confirmed'])
        self.assertTrue(result['upload_plan'][0]['replaces_existing'])
        self.assertEqual(result['problems'], [])
        self.assertEqual(draft.await_args.args[1], 'list')
        draft.assert_awaited_once()
        upload.assert_not_awaited()
        page.click.assert_not_awaited()
