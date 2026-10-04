#!/usr/bin/env python3
"""Offline source integrity checks. Never launch Codex or read private state."""
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codex_bar import __version__
from codex_bar.manager import read_assets


def main():
    read_assets(ROOT)
    files = []
    for folder in ('codex_bar', 'tests', 'scripts', 'platforms'):
        for path in (ROOT / folder).rglob('*.py'):
            ast.parse(path.read_text(), filename=str(path.relative_to(ROOT)))
            files.append(path)
    for name in ('LICENSE', 'README.md', 'NOTICE.md', 'docs/PROVENANCE.md',
                 'platforms/windows/README.md'):
        assert (ROOT / name).is_file(), name
    print(json.dumps({'version': __version__, 'pythonSourcesParsed': len(files),
                      'assetFingerprintsVerified': True, 'runtimeLaunched': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
