import os
import unittest
from unittest.mock import AsyncMock, patch
from moodle_mcp import browser


class LoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_login_detected_without_url_timeout(self):
        page = AsyncMock()
        page.url = 'https://accounts.google.com/signin?secret=hidden'
        with self.assertRaises(browser.MoodleLoginRequired) as error:
            await browser.goto(page, browser.BASE_URL + '/my/')
        self.assertNotIn('secret', str(error.exception))
        page.wait_for_url.assert_not_called()

    async def test_authenticated_page(self):
        page = AsyncMock()
        page.url = browser.BASE_URL + '/my/'
        page.evaluate.return_value = True
        await browser.goto(page, page.url)

    async def test_headless_login_error(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(browser.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(RuntimeError, '画面がありません'):
                await browser.login()

    async def test_read_rejected_during_login(self):
        with patch.object(browser, '_logging_in', True):
            with self.assertRaisesRegex(RuntimeError, '手動ログイン中'):
                async with browser.moodle_context():
                    self.fail('must not enter')
