import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from moodle_mcp import browser


class GetCourseTests(unittest.IsolatedAsyncioTestCase):
    async def get_course(self, sections):
        page = AsyncMock()
        page.evaluate.return_value = sections

        @asynccontextmanager
        async def context(*args):
            yield page

        with patch.object(browser, 'moodle_context', context), \
             patch.object(browser, 'new_tab', context), \
             patch.object(browser, 'goto', AsyncMock()), \
             patch.object(browser, '_find_course', AsyncMock(return_value={'id': 7, 'fullname': 'キャリア設計II'})):
            return await browser.get_course('キャリア')

    async def test_long_text_kept_up_to_limit(self):
        # 300 字を超えても後半の告知（休講など）が残る
        notice = 'あ' * 1500 + '第10回は休講'
        result = await self.get_course([
            {'name': '第10回', 'summary': notice, 'activities': [{'type': 'label', 'text': notice}]},
        ])
        section = result['sections'][0]
        self.assertEqual(section['summary'], notice)
        self.assertEqual(section['activities'][0]['text'], notice)

    async def test_over_limit_truncated_with_note(self):
        limit = browser.MAX_SECTION_TEXT_CHARS
        long = 'い' * (limit + 123)
        result = await self.get_course([
            {'name': '配属', 'summary': long, 'activities': [{'type': 'label', 'text': long}]},
        ])
        section = result['sections'][0]
        expected = 'い' * limit + '\n…（省略: 残り 123 文字）'
        self.assertEqual(section['summary'], expected)
        self.assertEqual(section['activities'][0]['text'], expected)

    async def test_text_tidied_and_activities_untouched(self):
        result = await self.get_course([
            {'name': '第1回', 'summary': '日程変更  \r\n\r\n\r\n\r\n11/20 に移動\xa0',
             'activities': [{'id': 5, 'type': 'resource', 'name': '資料'}]},
            {'name': '空', 'summary': '', 'activities': []},
        ])
        self.assertEqual(result['sections'], [
            {'name': '第1回', 'summary': '日程変更\n\n11/20 に移動',
             'activities': [{'id': 5, 'type': 'resource', 'name': '資料'}]},
        ])


if __name__ == '__main__':
    unittest.main()
