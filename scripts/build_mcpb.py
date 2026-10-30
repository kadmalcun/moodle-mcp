"""許可したソースだけから MCPB を作る。プロファイルや venv は含めない。"""
import json
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    if manifest['version'] != project['version']:
        raise ValueError('manifest と pyproject の version が一致しません')
    files = [ROOT / name for name in (
        'manifest.json', 'pyproject.toml', 'uv.lock', 'README.md', 'LICENSE', 'bundle/server.py',
        'docs/verification.md',
    )]
    files += sorted((ROOT / 'src' / 'moodle_mcp').glob('*.py'))
    target = ROOT / 'dist' / f"moodle-mcp-{project['version']}.mcpb"
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    print(target)


if __name__ == '__main__':
    main()
