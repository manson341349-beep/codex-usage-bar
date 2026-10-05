# Notices

Copyright (c) 2026 Codex Usage Bar contributors. Project-authored code, the original Sprig character geometry, materials, animation controller and 2D fallback artwork are provided under the MIT license in [LICENSE](LICENSE).

## Third-party rendering library

This release includes **Three.js 0.180.0 (r180)**, licensed under MIT. Copyright © 2010-2025 three.js authors. Its complete license is retained in [web/THREE-LICENSE.txt](web/THREE-LICENSE.txt) and included in the application payload.

The source distribution contains the upstream modules `web/vendor/three.module.js` and `web/vendor/three.core.min.js`. The installed application uses their bundled code in `web/sprig.js`; it does not fetch a third-party renderer from a CDN at runtime. Bundling does not change the upstream license or transfer authorship of Three.js to this project.

**esbuild 0.25.11** is a locked development dependency used to reproduce the bundle. It is not included as an executable or runtime dependency in the installed application. Its package retains its own upstream license when obtained through npm.

## Product film and editable animation project

The original [24-second product film](docs/assets/sprig-product-film.mp4), its poster, project-authored animation source in [promos/sprig-remotion](promos/sprig-remotion/README.en.md), Sprig character assets, and component fixture screenshots are project-authored visual materials under the root MIT license. The screenshots use the actual usage-bar component with synthetic example values; they do not contain real account usage or conversation content. The original synthesized soundtrack in `promos/sprig-remotion/public/audio/sprig-score.wav` is provided under **CC0-1.0** and contains no external music samples or recorded voices.

The animation project has its own dependency lockfile. **Remotion 4.0.532** is governed by the [upstream Remotion License](https://github.com/remotion-dev/remotion/blob/v4.0.532/LICENSE.md), not this project's MIT license. React, Playwright, Pillow, and other animation dependencies retain their respective upstream terms. The local Three.js copy retains [its own MIT notice](promos/sprig-remotion/asset-source/web/vendor/THREE-LICENSE.txt). Animation tools are separate from the installed usage-bar runtime; adding the film does not relicense those tools or the external browser and system fonts used to render it.

## External applications and references

Codex and OpenAI are names of their respective owners. This independent project is not affiliated with or endorsed by OpenAI. The separately installed Codex application and CLI, Python runtime, and macOS system tools are not bundled or relicensed by this project.

No code, extracted frontend, artwork, executable or third-party runtime from the early proprietary reference package is distributed here. Notices accompanying that reference are not evidence of an open-source license for the whole reference application. The Sprig character does not contain copied Spline or Rive character assets. See [docs/PROVENANCE.md](docs/PROVENANCE.md) for the scope of this release.
