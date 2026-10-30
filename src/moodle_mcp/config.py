"""利用者が明示的に変更する機能設定。"""
import os


def submissions_enabled() -> bool:
    # 不明な値・未指定は無効。MCPB の boolean は文字列 true/false として渡る。
    return os.environ.get('MOODLE_MCP_ENABLE_SUBMISSIONS', '').lower() in {'1', 'true'}


def require_submissions() -> None:
    if not submissions_enabled():
        raise PermissionError('提出ツールは無効です。有効にするには MOODLE_MCP_ENABLE_SUBMISSIONS=true を設定してください')
