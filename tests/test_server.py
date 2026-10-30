import os
import sys
import unittest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class StdioTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_tools_and_error(self):
        env = dict(os.environ)
        env['MOODLE_MCP_ENABLE_SUBMISSIONS'] = 'false'
        env.pop('DISPLAY', None)
        env.pop('WAYLAND_DISPLAY', None)
        params = StdioServerParameters(command=sys.executable,
                                      args=['-m', 'moodle_mcp.server'], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                self.assertEqual({t.name for t in tools}, {
                    'moodle_login', 'moodle_deadlines', 'moodle_course',
                    'moodle_announcements', 'moodle_activity', 'moodle_download_file'})
                result = await session.call_tool('moodle_deadlines', {'days_overdue': -1})
                self.assertTrue(result.isError)
                if sys.platform.startswith('linux'):
                    result = await session.call_tool('moodle_login', {})
                    self.assertTrue(result.isError)
                    self.assertIn('画面がありません', result.content[0].text)

    async def test_enabled_submission_tool_schema(self):
        params = StdioServerParameters(command=sys.executable,
            args=['-m', 'moodle_mcp.server'],
            env={**os.environ, 'MOODLE_MCP_ENABLE_SUBMISSIONS': 'true'})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                tool = tools['moodle_submit_files']
                self.assertTrue(tool.annotations.destructiveHint)
                self.assertFalse(tool.inputSchema['properties']['confirm']['default'])
                # 入力が不正な下見だけを呼ぶ。Moodle には接続しない。
                response = await session.call_tool('moodle_submit_files', {'cmid': 1, 'paths': []})
                self.assertTrue(response.isError)
