#!/usr/bin/env python3
"""Offline source integrity checks. Never launch Codex or read private state."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
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
                 'platforms/windows/README.md', 'platforms/macos/Launcher.swift'):
        assert (ROOT / name).is_file(), name
    swift_source = ROOT / 'platforms/macos/Launcher.swift'
    assert swift_source.read_text(encoding='utf-8').strip(), 'empty native launcher source'
    shell_scripts_checked = []
    zsh = shutil.which('zsh')
    if zsh:
        for name in ('Start.command', 'Test.command'):
            subprocess.run([zsh, '-n', str(ROOT / 'platforms/macos' / name)], check=True)
            shell_scripts_checked.append(name)
    print(json.dumps({'version': __version__, 'pythonSourcesParsed': len(files),
                      'assetFingerprintsVerified': True, 'swiftSourcePresent': True,
                      'nativeCompilationRequiredForBuild': True,
                      'shellScriptsSyntaxChecked': shell_scripts_checked,
                      'runtimeLaunched': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
