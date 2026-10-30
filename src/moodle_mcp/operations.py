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


async def submit_files(cmid: int, paths: list[str], confirm: bool = False) -> dict:
    """ローカルファイルを提出候補にする。既定は下見のみ。"""
    from .config import require_submissions
    require_submissions()
    if cmid <= 0:
        raise ValueError('cmid は正の整数にしてください')
    if not paths:
        raise ValueError('提出するファイルを1つ以上指定してください')
    files = []
    names = set()
    total = 0
    limit = 100 * 1024 * 1024
    for value in paths:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError(f'通常のファイルではありません: {path}')
        if path.name in names:
            raise ValueError(f'同名ファイルが重複しています: {path.name}')
        names.add(path.name)
        with path.open('rb') as stream:
            body = stream.read(limit - total + 1)
        total += len(body)
        if total > limit:
            raise ValueError('提出候補の合計は100 MiB以下にしてください')
        files.append((path.name, body))
    return await browser.save_submission(cmid, files, confirm=confirm)
