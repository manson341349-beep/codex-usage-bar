# Notices

Copyright (c) 2026 Codex Usage Bar contributors. Project-authored code, the original Sprig character geometry, materials, animation controller and 2D fallback artwork are provided under the MIT license in [LICENSE](LICENSE).

## Third-party rendering library

This release includes **Three.js 0.180.0 (r180)**, licensed under MIT. Copyright © 2010-2025 three.js authors. Its complete license is retained in [web/THREE-LICENSE.txt](web/THREE-LICENSE.txt) and included in the application payload.

The source distribution contains the upstream modules `web/vendor/three.module.js` and `web/vendor/three.core.min.js`. The installed application uses their bundled code in `web/sprig.js`; it does not fetch a third-party renderer from a CDN at runtime. Bundling does not change the upstream license or transfer authorship of Three.js to this project.

**esbuild 0.25.11** is a locked development dependency used to reproduce the bundle. It is not included as an executable or runtime dependency in the installed application. Its package retains its own upstream license when obtained through npm.

## External applications and references

Codex and OpenAI are names of their respective owners. This independent project is not affiliated with or endorsed by OpenAI. The separately installed Codex application and CLI, Python runtime, and macOS system tools are not bundled or relicensed by this project.

No code, extracted frontend, artwork, executable or third-party runtime from the early proprietary reference package is distributed here. Notices accompanying that reference are not evidence of an open-source license for the whole reference application. The Sprig character does not contain copied Spline or Rive character assets. See [docs/PROVENANCE.md](docs/PROVENANCE.md) for the scope of this release.
