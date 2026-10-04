/* MIT — Original Sprig companion. Three.js license: web/THREE-LICENSE.txt. */
import * as THREE from '../vendor/three.module.js';
import {makeStage} from './character.js';
import {PerformanceController} from './motion.js';

function mount(button,{root}={}) {
  if(!button||!root)throw new TypeError('Sprig requires its owned button and bar');
  const doc=button.ownerDocument,win=doc.defaultView;
  const fallback=button.querySelector('.cbu-sprig-fallback');
  const canvas=doc.createElement('canvas');canvas.className='cbu-sprig-canvas';canvas.setAttribute('aria-hidden','true');
  canvas.style.cssText='display:none;position:absolute;inset:0;width:56px;height:56px;pointer-events:none';button.append(canvas);
  const motion=new PerformanceController(),lifetime=new AbortController();
  const media=win.matchMedia('(prefers-reduced-motion: reduce)');
  let reduced=media.matches,allowed=false,disposed=false,failed=false,focused=doc.hasFocus(),intersecting=true;
  let renderer=null,stage=null,camera=null,gl=null,ext=null,queries=[],pointerEvents=null,raf=0,last=0,running=false,themeDirty=true,pose=motion.update(0);
  let inits=0,frames=0,drawCalls=0,triangles=0,intervals=[],cpu=[],gpu=[],warmFrames=0;
  let themeFingerprint='',mode='fallback',contextLost=false;
  const listen=(target,type,fn,options={})=>target.addEventListener(type,fn,{...options,signal:lifetime.signal});
  const sample=(arr,n)=>{arr.push(n);if(arr.length>600)arr.shift()};
  const pct=(arr,p)=>arr.length?+arr.slice().sort((a,b)=>a-b)[Math.floor((arr.length-1)*p)].toFixed(3):null;
  function canRender(){
    if(disposed||failed||!allowed||!intersecting||doc.hidden||!focused||!button.isConnected)return false;
    const rect=button.getBoundingClientRect(),style=win.getComputedStyle(button);
    return rect.width>0&&rect.height>0&&style.display!=='none'&&style.visibility==='visible';
  }
  function pause(){running=false;if(raf)win.cancelAnimationFrame(raf);raf=0;last=0;pointerEvents?.abort();pointerEvents=null;motion.setPointer(0,0,false)}
  function release(){
    const gs=new Set(),ms=new Set(),ts=new Set();
    stage?.scene.traverse(o=>{if(o.geometry)gs.add(o.geometry);if(o.material)for(const m of Array.isArray(o.material)?o.material:[o.material])ms.add(m);o.shadow?.dispose()});
    for(const m of ms){if(m.map)ts.add(m.map);m.dispose()}for(const g of gs)g.dispose();for(const t of ts)t.dispose();
    if(stage){stage.scene.environment=null;stage.env.dispose()}
    if(gl&&!gl.isContextLost())for(const q of queries)gl.deleteQuery(q);
    queries=[];renderer?.dispose();renderer?.forceContextLoss();renderer=stage=camera=gl=ext=null;
  }
  function fail(){failed=true;mode='fallback';pause();canvas.style.display='none';if(fallback)fallback.style.display='block';release()}
  function initialize(){
    if(renderer||failed||disposed)return;
    try{
      renderer=new THREE.WebGLRenderer({canvas,alpha:true,antialias:true,powerPreference:'low-power'});
      renderer.setPixelRatio(Math.min(win.devicePixelRatio||1,2));renderer.setSize(56,56,false);renderer.setClearColor(0,0);
      renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.14;
      renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
      stage=makeStage(renderer);stage.model.setDetail(false);
      camera=new THREE.OrthographicCamera(-1.775,1.775,1.775,-1.775,.1,30);camera.position.set(0,2.02,8);camera.lookAt(0,1.50,0);
      gl=renderer.getContext();ext=gl.getExtension('EXT_disjoint_timer_query_webgl2');inits++;mode='webgl';themeDirty=true;
    }catch(_){fail()}
  }
  function updateTheme(){
    if(!renderer||!stage)return;themeDirty=false;
    const css=win.getComputedStyle(root),surface=css.backgroundColor,accent=css.getPropertyValue('--cbu-accent').trim(),key=surface+'|'+accent;
    if(key===themeFingerprint)return;themeFingerprint=key;
    const parse=value=>{const probe=doc.createElement('span');probe.style.color=value;probe.style.display='none';root.append(probe);const c=win.getComputedStyle(probe).color;probe.remove();return new THREE.Color(c)};
    try{
      const bg=parse(surface),lightness=bg.r*.2126+bg.g*.7152+bg.b*.0722;
      renderer.toneMappingExposure=lightness<.16?1.02:1.14;
      // Keep the approved mint identity; a restrained accent tint follows custom themes.
      const tint=parse(accent||'#8bbdab');tint.lerp(new THREE.Color(0x68ad92),.82);
      stage.model.materials.mint.color.copy(tint);
    }catch(_){/* Invalid host custom properties cannot break quota or the character. */}
  }
  function readGPU(){if(!ext)return;while(queries.length){const q=queries[0];if(!gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE))break;if(!gl.getParameter(ext.GPU_DISJOINT_EXT)&&warmFrames>15)sample(gpu,gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6);gl.deleteQuery(q);queries.shift()}}
  function render(){
    if(!renderer||!canRender())return;
    try{
      const start=performance.now();if(themeDirty)updateTheme();
      const ratio=Math.min(win.devicePixelRatio||1,2);if(renderer.getPixelRatio()!==ratio){renderer.setPixelRatio(ratio);renderer.setSize(56,56,false)}
      stage.model.apply(pose);stage.contact.position.x=pose.x;stage.contact.material.opacity=Math.max(.2,1-pose.y*.9);stage.contact.scale.setScalar(1+pose.y*.65);
      readGPU();let q=null;if(ext&&queries.length<5){q=gl.createQuery();gl.beginQuery(ext.TIME_ELAPSED_EXT,q)}
      renderer.render(stage.scene,camera);if(q){gl.endQuery(ext.TIME_ELAPSED_EXT);queries.push(q)}
      frames++;warmFrames++;drawCalls=renderer.info.render.calls;triangles=renderer.info.render.triangles;if(warmFrames>15)sample(cpu,performance.now()-start);
      canvas.style.display='block';if(fallback)fallback.style.display='none';
    }catch(_){fail()}
  }
  function frame(time){
    raf=0;if(!canRender()){pause();return}const dt=last?Math.min((time-last)/1000,.05):1/60;
    if(last&&time-last<300&&warmFrames>15)sample(intervals,time-last);last=time;
    try{pose=motion.update(dt);render()}catch(_){fail()}
    if(!failed&&running&&!reduced)raf=win.requestAnimationFrame(frame);else running=false;
  }
  function act(state){if(!canRender())return;motion.setState(state);if(reduced){pose=motion.update(0);render()}else resume()}
  function attachPointer(){
    if(pointerEvents||!canRender())return;pointerEvents=new AbortController();
    const on=(type,fn)=>button.addEventListener(type,fn,{signal:pointerEvents.signal});
    on('pointerenter',e=>{if(e.pointerType!=='touch')act('hello')});
    on('pointermove',e=>{const b=button.getBoundingClientRect();if(!b.width||!b.height)return;motion.setPointer((e.clientX-b.left)/b.width*2-1,1-(e.clientY-b.top)/b.height*2,true);if(reduced){pose=motion.update(0);render()}});
    on('pointerleave',()=>{motion.setPointer(0,0,false);if(reduced){pose=motion.update(0);render()}});
    on('pointercancel',()=>motion.setPointer(0,0,false));
    on('click',()=>act('play'));on('focus',()=>act('hello'));
  }
  function resume(){
    if(!canRender()){pause();return}initialize();if(failed)return;attachPointer();
    if(reduced){pose=motion.update(0);render();return}
    if(!running){running=true;last=0;raf=win.requestAnimationFrame(frame)}
  }
  function setVisible(value){if(disposed)return;allowed=!!value;if(allowed)resume();else pause()}
  function setTheme(){if(disposed)return;themeDirty=true;if(canRender()&&reduced)render()}
  function inspect(){return {mode,state:pose.state,visible:canRender(),paused:!running,reducedMotion:reduced,frames,drawCalls,triangles,width:56,height:56,pixelRatio:renderer?.getPixelRatio()??null,initCount:inits,contextLost,pointerListening:!!pointerEvents,gpuTimerSupported:!!ext,rafMedianMs:pct(intervals,.5),rafP95Ms:pct(intervals,.95),cpuP95Ms:pct(cpu,.95),gpuP95Ms:pct(gpu,.95),rafSamples:intervals.length,gpuSamples:gpu.length}}
  const intersection=new IntersectionObserver(entries=>{intersecting=entries[entries.length-1]?.isIntersecting??true;resume()});intersection.observe(button);
  const resize=new ResizeObserver(()=>resume());resize.observe(button);
  const theme=new MutationObserver(records=>{if(records.some(r=>r.target!==root&&!root.contains(r.target))){themeDirty=true;if(canRender()&&reduced)render()}});
  theme.observe(doc.documentElement,{subtree:true,attributes:true,attributeFilter:['class','style','data-theme']});
  listen(doc,'visibilitychange',()=>doc.hidden?pause():resume());
  listen(win,'blur',()=>{focused=false;pause()});listen(win,'focus',()=>{focused=true;resume()});
  listen(media,'change',e=>{reduced=e.matches;motion.setReduced(reduced);pose=motion.update(0);pause();resume()});
  listen(canvas,'webglcontextlost',e=>{e.preventDefault();contextLost=true;fail()});
  function destroy(){if(disposed)return;disposed=true;pause();lifetime.abort();intersection.disconnect();resize.disconnect();theme.disconnect();release();canvas.remove();if(fallback)fallback.style.display='block'}
  motion.setReduced(reduced);
  return Object.freeze({setVisible,setTheme,destroy,inspect});
}
window.CodexUsageBarSprig=Object.freeze({mount});
