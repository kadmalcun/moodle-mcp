"""MCP と CLI に共通のローカルファイル操作。"""
from pathlib import Path
from . import browser


async def download_file(output: str, cmid: int | None = None, url: str | None = None) -> dict:
    """指定したパスに保存する。既存ファイルは上書きしない。"""
    target = Path(output).expanduser().resolve()
    if target.exists():
        raise FileExistsError(f"保存先は既に存在します: {target}")
    if not target.parent.is_dir():
        raise ValueError(f"保存先のディレクトリがありません: {target.parent}")
    result = await browser.fetch_file(cmid=cmid, url=url)
    with target.open('xb') as stream:
        stream.write(result['body'])
    return {'path': str(target), 'name': result['name'],
            'content_type': result['content_type'], 'size': len(result['body'])}
