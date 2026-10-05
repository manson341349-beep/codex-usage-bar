# Sprig · Remotion product film

[English](README.en.md) | [简体中文](README.md) | [Back to the project](../../README.en.md)

An original 24-second product film at 1080 × 1350 and 60 fps. This project includes the editable timeline, character model and motion, screenshots of the actual component with synthetic data, original audio, and locked dependencies. The 420 regenerable transparent character frames are omitted from the repository and generated locally on first use.

[Watch the original film](https://github.com/manson341349-beep/codex-usage-bar/blob/main/docs/assets/sprig-product-film.mp4) · [Download MP4](https://raw.githubusercontent.com/manson341349-beep/codex-usage-bar/main/docs/assets/sprig-product-film.mp4)

## First run

Install **Node.js 22+, Python 3.12+, and Google Chrome** beforehand. Image processing requires exactly **Pillow 12.0.0**. This animation project is independent of the plugin build at the repository root: install its dependencies and run its commands inside `promos/sprig-remotion/`. It does not need to launch or connect to Codex.

These commands were verified on macOS. Start from the repository root:

```sh
cd promos/sprig-remotion
npm ci --ignore-scripts
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-assets.txt
PYTHON=.venv/bin/python npm run assets
PYTHON=.venv/bin/python npm run studio
```

Dependency installation accesses npm and the Python package index. If your existing Python already has Pillow 12.0.0, you can skip the virtual environment and use `npm run assets` and `npm run studio`. For asset generation and video export, set `REMOTION_BROWSER_EXECUTABLE` to an installed browser executable if necessary; macOS checks the standard Google Chrome installation path by default.

`npm run assets` renders the local Three.js modules, character model, and motion into 1024-pixel masters, then uses Pillow LANCZOS resizing to create 768-pixel transparent PNGs. It produces 300 frames in `public/sprig/performance/` and 120 in `public/sprig/idle/`. A complete set with a matching source fingerprint is reused automatically. To force regeneration, run `PYTHON=.venv/bin/python npm run assets -- --force`.

## Edit and export

```sh
npm run check
PYTHON=.venv/bin/python npm run stills
PYTHON=.venv/bin/python npm run render
```

`check` runs TypeScript type checking. `studio`, `stills`, and `render` first check the character assets and regenerate them if needed, so keep the `PYTHON` setting when using the virtual environment. Studio provides a timeline preview. Keyframe PNGs and the H.264/AAC film are written to `out/`; the video filename is `Sprig-Remotion-Product-Film.mp4`.

Asset generation and film export have been verified on macOS. Other platforms have not been fully validated. Adjust virtual environment paths for your system; substituted fonts, browser versions, and GPU drivers can also affect glyphs, layout, or rasterized pixels. System fonts are not bundled, and rebuilds on other machines are not guaranteed to be byte-identical.

## Files

- `src/SprigFilm.tsx`: scenes, copy, camera framing, and animation timeline.
- `src/index.tsx`: the `Sprig-Product-Film` composition, dimensions, 1440-frame duration, and 60 fps setting.
- `scripts/rebuild-sprig.mjs` and `asset-source/`: character source, browser rendering, and transparent PNG preparation.
- `scripts/render.mjs`: still and video export.
- `public/ui/`: the project's actual component rendered with synthetic data in Chinese/English, light/dark, and expanded/collapsed states.
- `public/audio/sprig-score.wav`: the original 24-second synthesized score and interaction sounds, without voiceover or external music samples.
- `package-lock.json` and `requirements-assets.txt`: locked dependencies for this independent animation project.

## Demonstration and licensing boundaries

The film's 72% weekly remaining and 18% five-hour usage are synthetic examples; cache statistics are unknown. UI screenshots come from isolated component fixtures, not a user's account or conversations. The composer shell and switching controls in the film are visual demonstrations. Character motion is rendered on an offline timeline: 60 fps is the encoded film rate, not evidence of sustained plugin presentation performance or a connection to real Codex work events. The main content remains understandable without sound.

Project-authored animation code, character assets, film visuals, and fixture screenshots use the root [MIT License](../../LICENSE). The original synthesized soundtrack is provided under **CC0-1.0**. Three.js retains its [MIT license](asset-source/web/vendor/THREE-LICENSE.txt) alongside the generation source. Remotion **4.0.532** and other dependencies follow their respective upstream licenses; the [Remotion License](https://github.com/remotion-dev/remotion/blob/v4.0.532/LICENSE.md) is separate from this repository's MIT grant. These animation dependencies are not needed to install or run the everyday plugin. See [NOTICE](../../NOTICE.md) and [PROVENANCE](../../docs/PROVENANCE.md) for the full source boundaries.
