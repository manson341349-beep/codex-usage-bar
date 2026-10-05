import {chromium} from 'playwright-core';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

// Portable original-model render. No connection to Codex, no external model, no wall-clock capture.
const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const repo=path.join(project,'asset-source');
const size=1024,fps=60,args=process.argv.slice(2);
const value=flag=>{const at=args.indexOf(flag);if(at<0)return null;if(!args[at+1]||args[at+1].startsWith('--'))throw Error(flag+' needs a value');return args[at+1];};
const verifyOnly=args.includes('--verify-only'),strict=args.includes('--strict');
const output=path.resolve(value('--output-dir')||path.join(project,'public','sprig'));
const sourceFiles=['web/vendor/three.module.js','web/vendor/three.core.min.js','web/sprig-source/character.js','web/sprig-source/motion.js','prepare.py','reference-pixels.json'];
const sourceHasher=crypto.createHash('sha256');
for(const name of sourceFiles){sourceHasher.update(name);sourceHasher.update(await fs.readFile(path.join(repo,name)));}
sourceHasher.update(await fs.readFile(fileURLToPath(import.meta.url)));
const sourceFingerprint=sourceHasher.digest('hex');
// A full marker plus all expected files is required. Partial/old renders rebuild.
if(!verifyOnly&&!args.includes('--force')){
 try{
  const prior=JSON.parse(await fs.readFile(path.join(output,'rebuild-report.json'),'utf8'));
  if(prior.frames===420&&!prior.keyframesOnly&&prior.sourceFingerprint===sourceFingerprint&&prior.allEdgesTransparent&&(!strict||prior.allReferencePixelsMatch)){
   const expected=[];for(const [clip,count] of [['performance',300],['idle',120]])for(let i=0;i<count;i++)expected.push(path.join(output,clip,`frame-${String(i).padStart(4,'0')}.png`));
   const checks=await Promise.all(expected.map(async file=>{try{const s=await fs.stat(file);return s.isFile()&&s.size>0}catch{return false}}));
   if(checks.every(Boolean)){console.log(JSON.stringify({complete:true,skipped:true,frames:420,output,reason:'Matching complete marker and all 420 nonempty PNGs exist.'}));process.exit(0);}
  }
 }catch{/* Missing or obsolete marker: rebuild. */}
}
const python=process.env.PYTHON||process.env.SPRIG_PYTHON||(process.platform==='win32'?'python':'python3');
const preflight=spawnSync(python,['-c',"import PIL; assert PIL.__version__ == '12.0.0', 'Install Pillow==12.0.0; found '+PIL.__version__"],{encoding:'utf8'});
if(preflight.status!==0)throw Error('Python/Pillow preflight failed. Install asset-source/requirements.txt. '+(preflight.error?.message||preflight.stderr));
const candidates=[process.env.REMOTION_BROWSER_EXECUTABLE,process.env.SPRIG_CHROME_PATH,
 '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
 '/usr/bin/google-chrome','/usr/bin/google-chrome-stable','/usr/bin/chromium','/usr/bin/chromium-browser',
 process.env.PROGRAMFILES&&path.join(process.env.PROGRAMFILES,'Google/Chrome/Application/chrome.exe'),
 process.env.LOCALAPPDATA&&path.join(process.env.LOCALAPPDATA,'Google/Chrome/Application/chrome.exe')].filter(Boolean);
let executablePath;for(const candidate of candidates){try{await fs.access(candidate);executablePath=candidate;break}catch{}}
if(!executablePath)throw Error('Chrome/Chromium not found. Set REMOTION_BROWSER_EXECUTABLE to an installed browser executable.');
const allowed=new Set(['/web/vendor/three.module.js','/web/vendor/three.core.min.js','/web/sprig-source/character.js','/web/sprig-source/motion.js']);
const html=`<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:transparent}canvas{display:block}</style><script type="module">
import * as THREE from '/web/vendor/three.module.js';
import {makeStage} from '/web/sprig-source/character.js';
import {PerformanceController} from '/web/sprig-source/motion.js';
const renderer=new THREE.WebGLRenderer({alpha:true,antialias:true,preserveDrawingBuffer:true,powerPreference:'high-performance'});
renderer.setPixelRatio(1);renderer.setSize(${size},${size});renderer.setClearColor(0,0);
renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.14;
renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
document.body.append(renderer.domElement);
const stage=makeStage(renderer);stage.model.setDetail(false);
// The installed short-ear, broad-face proportions are retained. At hero scale
// a higher shadow map removes projection stair-steps without changing the rig.
stage.key.shadow.mapSize.set(2048,2048);
const camera=new THREE.OrthographicCamera(-1.85,1.85,1.85,-1.85,.1,30);
camera.position.set(0,2.08,8);camera.lookAt(0,1.55,0);camera.updateMatrixWorld();
const floorPoint=new THREE.Vector3(0,.008,0).project(camera);
const groundAnchor={x:(floorPoint.x+1)/2,y:(1-floorPoint.y)/2};
const clips={};
function score(name,count,events){const actor=new PerformanceController();const poses=[];
 for(let i=0;i<count;i++){if(i)actor.update(1/${fps});if(events[i])actor.setState(events[i]);poses.push(actor.update(0));}clips[name]=poses;
}
score('greet-play',300,{0:'hello',114:'play'});
score('idle',120,{});
window.assetMeta=()=>({width:${size},height:${size},fps:${fps},groundAnchor,gl:renderer.getContext().getParameter(renderer.getContext().VERSION),clips:Object.fromEntries(Object.entries(clips).map(([name,poses])=>[name,poses.map((p,i)=>({frame:i,time:i/${fps},state:p.state,phase:p.phase,x:p.x,y:p.y,sy:p.sy,headX:p.headX,headY:p.headY,headZ:p.headZ,blink:p.blink,wink:p.wink}))]))});
window.exportFrame=(clip,index)=>{const p=clips[clip][index];stage.model.apply(p);stage.contact.position.x=p.x;stage.contact.material.opacity=Math.max(.2,1-p.y*.9);stage.contact.scale.setScalar(1+p.y*.65);renderer.render(stage.scene,camera);return {data:renderer.domElement.toDataURL('image/png'),calls:renderer.info.render.calls,triangles:renderer.info.render.triangles};};
window.ready=true;
</script>`;
const server=http.createServer(async(req,res)=>{try{const url=new URL(req.url,'http://localhost');if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(html);return}if(!allowed.has(url.pathname)){res.statusCode=404;res.end();return}res.setHeader('Content-Type','text/javascript');res.end(await fs.readFile(path.join(repo,url.pathname)))}catch{res.statusCode=500;res.end('Asset read failed')}});
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'sprig-rebuild-'));
let browser;const errors=[],started=Date.now();
try{
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve)});
 browser=await chromium.launch({executablePath,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const page=await browser.newPage({viewport:{width:size,height:size},deviceScaleFactor:1});
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.ready);
 const metadata=await page.evaluate(()=>window.assetMeta());
 const records=[];
 for(const [clip,poses] of Object.entries(metadata.clips)){
  const frames=verifyOnly?(clip==='greet-play'?[0,43,114,195,299]:[0,43,119]):poses.map((_,i)=>i);
  const folder=path.join(temp,clip);await fs.mkdir(folder,{recursive:true});
  for(const frame of frames){
   const rendered=await page.evaluate(({clip,frame})=>window.exportFrame(clip,frame),{clip,frame});
   const file=path.join(folder,String(frame).padStart(4,'0')+'.png');
   await fs.writeFile(file,Buffer.from(rendered.data.split(',')[1],'base64'));
   records.push({clip:clip==='greet-play'?'performance':'idle',frame,master:file});
   if(frame%60===0||verifyOnly)console.log(JSON.stringify({clip,frame,mode:verifyOnly?'keyframes':'all'}));
  }
 }
 if(errors.length)throw Error(JSON.stringify(errors));
 const input=path.join(temp,'frames.json');
 await fs.writeFile(input,JSON.stringify({fps,groundAnchor:metadata.groundAnchor,gl:metadata.gl,browser:browser.version(),records,verifyOnly,sourceFingerprint}));
 // Pillow's RGBA resize uses premultiplied-alpha LANCZOS, exactly as the original film assets.
 const result=spawnSync(python,[path.join(repo,'prepare.py'),input,output,...(strict?['--strict']:[])],{encoding:'utf8',maxBuffer:4*1024*1024});
 if(result.stdout)process.stdout.write(result.stdout);if(result.stderr)process.stderr.write(result.stderr);
 if(result.status!==0)throw Error('PNG preparation or strict reference check failed');
 console.log(JSON.stringify({complete:true,output,frames:records.length,elapsedSeconds:+((Date.now()-started)/1000).toFixed(2)}));
}finally{await browser?.close();if(server.listening)await new Promise(resolve=>server.close(resolve));await fs.rm(temp,{recursive:true,force:true});}
