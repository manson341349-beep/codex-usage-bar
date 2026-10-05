#!/usr/bin/env python3
"""Build a native macOS launcher from allowlisted source; no dependency downloads."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

PRODUCT = 'codex-usage-bar'
BUNDLE_ID = 'io.github.codex-usage-bar.local'
SOURCE_ROOT = Path(__file__).resolve().parents[2]
MARKER_NAME = 'codex-usage-bar-app.json'
MARKER = {'schemaVersion': 1, 'product': PRODUCT, 'bundleIdentifier': BUNDLE_ID}
# Deliberately enumerate files: no recursive source-directory copies.
PAYLOAD_FILES = (
    'codex_bar/__init__.py', 'codex_bar/__main__.py', 'codex_bar/manager.py',
    'codex_bar/host.py', 'codex_bar/daily_host.py', 'codex_bar/cdp.py', 'codex_bar/quota.py',
    'web/bar.js', 'web/bar.css', 'web/adaptive.js', 'web/sprig.js',
    'web/THREE-LICENSE.txt', 'web/asset-manifest.json',
    'platforms/macos/Launcher.swift', 'platforms/macos/Start.command',
    'platforms/macos/Test.command', 'platforms/macos/install.py',
    'platforms/macos/build.py', 'platforms/macos/resident.py',
    'README.md', 'LICENSE', 'NOTICE.md', 'docs/PROVENANCE.md',
    'docs/VALIDATION.md', 'README.en.md', 'docs/assets/overview.svg', 'docs/assets/overview.en.svg',
)
SOURCE_EXTRA_FILES = (
    '.gitignore', 'web/preview.html', 'platforms/macos/test_packaging.py',
    'platforms/macos/test_resident.py',
    'tests/test_cdp.py', 'tests/test_host.py', 'tests/test_daily_host.py',
    'tests/test_manager.py', 'tests/test_quota.py',
    'tests/test_frontend.js', 'tests/test_sprig_runtime.js', 'tests/test_i18n.js',
    'platforms/windows/README.md', '.github/workflows/ci.yml', 'scripts/validate.py',
    'web/sprig-source/runtime.js', 'web/sprig-source/character.js',
    'web/sprig-source/motion.js', 'web/vendor/three.module.js',
    'web/vendor/three.core.min.js', 'scripts/build-sprig.mjs',
    'package.json', 'package-lock.json',
)
OPTIONAL_SOURCE_FILES = (
    'tests/test_assets.py',
)
NATIVE_SOURCE = 'platforms/macos/Launcher.swift'
MACHO_MAGICS = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
                b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
                b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


class PackageError(RuntimeError):
    pass


def reject_symlink_components(path: Path) -> None:
    """Inspect existing path components without following a symlink."""
    path = path.absolute()
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise PackageError('symlink_path_refused')


def source_file(root: Path, relative: str) -> Path:
    candidate = root / relative
    reject_symlink_components(candidate)
    try:
        info = candidate.lstat()
    except FileNotFoundError as exc:
        raise PackageError('required_payload_file_missing: ' + relative) from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PackageError('unsafe_payload_file: ' + relative)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise PackageError('payload_file_not_user_owned: ' + relative)
    return candidate


def payload_bytes(root: Path, relative: str) -> bytes:
    source = source_file(root, relative)
    # Open no-follow as well as rejecting symlink path components.
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise PackageError('unsafe_payload_file: ' + relative)
        return stream.read()


def package_version(root: Path) -> str:
    """Read the package's literal version without importing or executing source."""
    try:
        tree = ast.parse(payload_bytes(root, 'codex_bar/__init__.py'))
        values = [node.value.value for node in tree.body
                  if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                  and any(isinstance(target, ast.Name) and target.id == '__version__'
                          for target in node.targets)]
    except (SyntaxError, ValueError) as exc:
        raise PackageError('invalid_package_version') from exc
    if (len(values) != 1 or not isinstance(values[0], str)
            or not re.fullmatch(r'\d+\.\d+\.\d+', values[0])):
        raise PackageError('invalid_package_version')
    return values[0]


def release_files(root: Path, *, source=False) -> list[str]:
    files = list(PAYLOAD_FILES)
    if source:
        files.extend(SOURCE_EXTRA_FILES)
        files.extend(name for name in OPTIONAL_SOURCE_FILES if (root / name).exists())
    return files


def compile_native_launcher(payload: Path, destination: Path, cache: Path) -> None:
    """Compile only copied release source, using the installed Apple toolchain."""
    architecture = platform.machine()
    if architecture not in ('arm64', 'x86_64'):
        raise PackageError('unsupported_macos_build_architecture')
    # Relative source paths and prefix maps avoid putting local source/account paths
    # in the distributed executable. No debug information is requested.
    command = ['/usr/bin/xcrun', 'swiftc', '-O', '-framework', 'AppKit',
               '-target', architecture + '-apple-macosx12.0',
               '-module-cache-path', str(cache),
               '-file-prefix-map', str(payload) + '=.',
               '-debug-prefix-map', str(payload) + '=.',
               NATIVE_SOURCE, '-o', os.path.relpath(destination, payload)]
    try:
        result = subprocess.run(command, cwd=payload, capture_output=True, timeout=120,
                                check=False)
    except FileNotFoundError as exc:
        raise PackageError('apple_swift_compiler_required') from exc
    except subprocess.TimeoutExpired as exc:
        raise PackageError('native_launcher_compile_timeout') from exc
    if result.returncode:
        # Compiler output may contain local paths; do not put it into release logs.
        raise PackageError('native_launcher_compile_failed')


def native_launcher_manifest(payload: Path, launcher: Path) -> dict:
    """Verify compiler output is a local regular Mach-O file, then record its hash."""
    reject_symlink_components(launcher)
    try:
        info = launcher.lstat()
    except FileNotFoundError as exc:
        raise PackageError('native_launcher_output_missing') from exc
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid()):
        raise PackageError('unsafe_native_launcher_output')
    data = launcher.read_bytes()
    if data[:4] not in MACHO_MAGICS:
        raise PackageError('native_launcher_not_macho')
    launcher.chmod(0o755)
    return {'path': 'Contents/MacOS/' + PRODUCT, 'format': 'Mach-O',
            'sha256': hashlib.sha256(data).hexdigest(), 'source': NATIVE_SOURCE,
            'sourceSha256': hashlib.sha256(payload_bytes(payload, NATIVE_SOURCE)).hexdigest(),
            'minimumMacOSVersion': '12.0'}


def build_source_archive(destination: Path, *, _source_root: Path = SOURCE_ROOT) -> dict:
    """Create a reproducible allowlisted source ZIP, never a recursive snapshot."""
    root, destination = _source_root.absolute(), destination.absolute()
    reject_symlink_components(root)
    reject_symlink_components(destination)
    if destination.exists():
        raise PackageError('build_destination_already_exists')
    version = package_version(root)
    files = [(relative, payload_bytes(root, relative))
             for relative in release_files(root, source=True)]
    manifest = {'schemaVersion': 1, 'product': PRODUCT, 'version': version, 'files': []}
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix='.codex-usage-bar-source-', dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'w+b') as stream, zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for relative, data in files:
                mode = 0o755 if relative.endswith('.command') else 0o644
                manifest['files'].append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest(),
                                          'mode': oct(mode)})
                entry = zipfile.ZipInfo(f'{PRODUCT}-{version}/{relative}', (1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | mode) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, data)
            entry = zipfile.ZipInfo(f'{PRODUCT}-{version}/source-manifest.json', (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, json.dumps(manifest, indent=2) + '\n')
        temporary.chmod(0o644)
        # Exclusive link means another process cannot make us overwrite its destination.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def build_app(destination: Path, *, _source_root: Path = SOURCE_ROOT) -> dict:
    """Create a new destination only. Private paths are never embedded in payloads."""
    root = _source_root.absolute()
    destination = destination.absolute()
    reject_symlink_components(root)
    reject_symlink_components(destination)
    if destination.exists():
        raise PackageError('build_destination_already_exists')
    version = package_version(root)
    files = release_files(root)
    for relative in files:
        source_file(root, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.codex-usage-bar-build-', dir=destination.parent))
    app = staging / (PRODUCT + '.app')
    resources = app / 'Contents' / 'Resources'
    payload = resources / PRODUCT
    manifest = {'schemaVersion': 1, 'product': PRODUCT, 'version': version, 'files': []}
    try:
        (app / 'Contents' / 'MacOS').mkdir(parents=True)
        for relative in files:
            target = payload / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            data = payload_bytes(root, relative)
            target.write_bytes(data)
            mode = 0o755 if relative.endswith('.command') else 0o644
            target.chmod(mode)
            manifest['files'].append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest(),
                                      'mode': oct(mode)})
        launcher = app / 'Contents' / 'MacOS' / PRODUCT
        compile_native_launcher(payload, launcher, staging / '.swift-module-cache')
        manifest['nativeLauncher'] = native_launcher_manifest(payload, launcher)
        plist = {'CFBundleName': PRODUCT, 'CFBundleDisplayName': PRODUCT,
                 'CFBundleIdentifier': BUNDLE_ID, 'CFBundleExecutable': PRODUCT,
                 'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': version,
                 'CFBundleVersion': version, 'LSUIElement': True,
                 'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '12.0'}
        (app / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(plist, sort_keys=True))
        (resources / MARKER_NAME).write_text(json.dumps(MARKER, indent=2) + '\n', encoding='utf-8')
        (resources / 'payload-manifest.json').write_text(
            json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        for metadata in (app / 'Contents' / 'Info.plist', resources / MARKER_NAME,
                         resources / 'payload-manifest.json'):
            metadata.chmod(0o644)
        for folder in [app, *(p for p in app.rglob('*') if p.is_dir())]:
            folder.chmod(0o755)
        app.rename(destination)
    finally:
        shutil.rmtree(staging)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        help='new .app directory to create; existing paths are refused')
    parser.add_argument('--source-output', type=Path,
                        help='create an allowlisted source ZIP; pass --output as well to build both')
    args = parser.parse_args(argv)
    try:
        if args.output is not None or args.source_output is None:
            output = args.output or SOURCE_ROOT / 'dist' / (PRODUCT + '.app')
            result = build_app(output)
            print(f'Built {output} ({len(result["files"])} allowlisted files; version {result["version"]}).')
        if args.source_output is not None:
            result = build_source_archive(args.source_output)
            print(f'Built {args.source_output} ({len(result["files"])} allowlisted source files).')
    except (PackageError, OSError) as exc:
        print('Build refused: ' + str(exc))
        return 1
    print('Local native launcher; no notarization or Gatekeeper bypass is included.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
