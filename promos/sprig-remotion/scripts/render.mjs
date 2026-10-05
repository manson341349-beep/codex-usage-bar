import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const installedChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browserExecutable = process.env.REMOTION_BROWSER_EXECUTABLE || (existsSync(installedChrome) ? installedChrome : undefined);
const serveUrl = await bundle({entryPoint: path.join(root, 'src/index.tsx'), publicDir: path.join(root, 'public')});
const composition = await selectComposition({serveUrl, id: 'Sprig-Product-Film', browserExecutable});
const out = path.join(root, 'out');
await fs.mkdir(out, {recursive: true});
const stillFrames = [90, 195, 340, 530, 642, 720, 915, 1000, 1090, 1230, 1390];
if (process.argv.includes('--stills')) {
  for (const frame of stillFrames) {
    await renderStill({serveUrl, composition, frame, imageFormat: 'png', output: path.join(out, `frame-${String(frame).padStart(4, '0')}.png`), browserExecutable});
    console.log(`Still ${frame}/1440`);
  }
} else {
  let last = -1;
  await renderMedia({serveUrl, composition, codec: 'h264', pixelFormat: 'yuv420p', crf: 18,
    audioCodec: 'aac', audioBitrate: '192k', sampleRate: 48000, concurrency: 4,
    x264Preset: 'medium', browserExecutable, outputLocation: path.join(out, 'Sprig-Remotion-Product-Film.mp4'),
    metadata: {title: 'codex-usage-bar — Meet Sprig', comment: 'Original Remotion film. All quota values are illustrative.'},
    onProgress: ({progress, renderedFrames, encodedFrames}) => {
      const p = Math.floor(progress * 10);
      if (p !== last) {last = p; console.log(JSON.stringify({percent: p * 10, renderedFrames, encodedFrames}));}
    }});
  console.log('Rendered 24 seconds, 1080×1350, 60 fps.');
}
