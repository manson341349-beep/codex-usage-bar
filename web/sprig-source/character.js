import * as THREE from '../vendor/three.module.js';
const C={mint:0x68ad92,light:0x99c7a8,cream:0xffefce,dark:0x182e2c,peach:0xefa780,gold:0xf0c475};
function roundShape(w,h,r){const s=new THREE.Shape(),x=-w/2,y=-h/2;s.moveTo(x+r,y);s.lineTo(x+w-r,y);s.quadraticCurveTo(x+w,y,x+w,y+r);s.lineTo(x+w,y+h-r);s.quadraticCurveTo(x+w,y+h,x+w-r,y+h);s.lineTo(x+r,y+h);s.quadraticCurveTo(x,y+h,x,y+h-r);s.lineTo(x,y+r);s.quadraticCurveTo(x,y,x+r,y);return s;}
function boxGeom(w,h,d,r=.16){const g=new THREE.ExtrudeGeometry(roundShape(w-r*2,h-r*2,r),{depth:d-r*2,bevelEnabled:true,bevelSegments:5,steps:1,bevelSize:r,bevelThickness:r,curveSegments:12});g.translate(0,0,-(d-r*2)/2);g.computeVertexNormals();return g;}
function ellipsoid(w,h,d,p=.8){const g=new THREE.SphereGeometry(1,48,32),a=g.attributes.position;const pow=v=>Math.sign(v)*Math.pow(Math.abs(v),p);for(let i=0;i<a.count;i++){a.setXYZ(i,pow(a.getX(i))*w,pow(a.getY(i))*h,pow(a.getZ(i))*d)}g.computeVertexNormals();return g;}
function mat(color,roughness=.48){return new THREE.MeshPhysicalMaterial({color,roughness,metalness:0,clearcoat:.17,clearcoatRoughness:.38,sheen:.22,sheenColor:0xffffff,sheenRoughness:.7});}
export function makeCharacter(){
 const root=new THREE.Group(),body=new THREE.Group();root.name='Sprig';body.name='Sprig/body';root.add(body);
 const mint=mat(C.mint),light=mat(C.light),cream=mat(C.cream,.56),dark=mat(C.dark,.32),peach=mat(C.peach,.6),gold=mat(C.gold,.36),white=mat(0xfffdf2,.2);
 const mesh=(geo,material,parent,pos=[0,0,0])=>{let m=new THREE.Mesh(geo,material);m.position.set(...pos);m.castShadow=true;m.receiveShadow=true;parent.add(m);return m};
 const ball=(sz,material,parent,pos)=>mesh(new THREE.SphereGeometry(1,32,24),material,parent,pos).scale.set(...sz);
 // A squat pear body and oversized soft mask. All modelled from original geometry.
 mesh(ellipsoid(.49,.57,.35,.83),mint,body,[0,.73,0]);
 mesh(ellipsoid(.315,.34,.1,.9),cream,body,[0,.69,.306]);
 const seam=mesh(new THREE.TorusGeometry(.36,.019,8,64),light,body,[0,.83,0]);seam.rotation.x=Math.PI/2;seam.scale.set(1.25,1,1);
 const feet=[];for(const side of [-1,1]){const f=new THREE.Group();f.position.set(side*.285,.16,.13);body.add(f);mesh(ellipsoid(.245,.17,.30,.85),mint,f);mesh(ellipsoid(.2,.065,.23,.8),light,f,[0,-.10,.025]);feet.push(f)}
 const arms=[];for(const side of [-1,1]){const arm=new THREE.Group();arm.position.set(side*.475,1.0,.045);body.add(arm);let m=mesh(ellipsoid(.145,.31,.15,.95),mint,arm,[side*.026,-.22,0]);m.rotation.z=side*.12;mesh(ellipsoid(.153,.155,.163,.94),light,arm,[side*.055,-.41,.055]);arms.push(arm)}
 const headPivot=new THREE.Group();headPivot.name='Sprig/head';headPivot.position.set(0,1.38,.015);body.add(headPivot);
 const head=mesh(ellipsoid(.79,.65,.54,.72),mint,headPivot,[0,.27,0]);
 // Sculpted, domed cream face. Broad shapes survive 56px rendering.
 mesh(ellipsoid(.644,.435,.132,.68),cream,headPivot,[0,.225,.477]);
 const ears=[];for(const side of [-1,1]){const ear=new THREE.Group();ear.position.set(side*.50,.76,-.045);headPivot.add(ear);ear.rotation.z=side*-.34;const tall=side===-1?.45:.36;mesh(ellipsoid(.205,tall,.165,.90),mint,ear,[0,tall*.62,0]);mesh(ellipsoid(.111,tall*.66,.045,.95),side===-1?light:peach,ear,[0,tall*.64,.136]);ears.push(ear)}
 // One warm pin in the short ear is a recognisable asymmetrical signature.
 const pin=mesh(new THREE.SphereGeometry(.072,24,16),gold,ears[1],[.06,.405,.149]);
 const eyes=[];for(const side of [-1,1]){const e=new THREE.Group();e.name=side===-1?'Sprig/eye-left':'Sprig/eye-right';e.position.set(side*.245,.28,.599);headPivot.add(e);mesh(ellipsoid(.115,.158,.073,.8),dark,e);mesh(new THREE.SphereGeometry(.032,16,12),white,e,[-.028,.057,.062]);eyes.push(e);mesh(ellipsoid(.105,.04,.018,1),peach,headPivot,[side*.425,.082,.589]);}
 const nose=mesh(ellipsoid(.049,.032,.025,.9),gold,headPivot,[0,.14,.622]);
 const mouthGroup=new THREE.Group();mouthGroup.position.set(0,.012,.62);headPivot.add(mouthGroup);
 const mouthCurve=new THREE.CatmullRomCurve3([new THREE.Vector3(-.09,.035,0),new THREE.Vector3(-.054,0,0),new THREE.Vector3(0,-.014,0),new THREE.Vector3(.054,0,0),new THREE.Vector3(.09,.035,0)]);
 const mouth=mesh(new THREE.TubeGeometry(mouthCurve,24,.012,8,false),dark,mouthGroup);
 const openMouth=mesh(ellipsoid(.071,.070,.015,1),dark,mouthGroup,[0,-.023,-.002]);openMouth.visible=false;
 mesh(ellipsoid(.039,.021,.01,1),peach,openMouth,[0,-.035,.01]);
 // Porcelain seed badge with a raised leaf rather than text at icon scale.
 mesh(ellipsoid(.092,.116,.035,.8),gold,body,[0,.76,.414]);const leaf=mesh(ellipsoid(.027,.056,.018,1),cream,body,[0,.767,.449]);leaf.rotation.z=-.5;
 const tail=new THREE.Group();tail.position.set(.35,.6,-.25);body.add(tail);const tm=mesh(ellipsoid(.24,.19,.42,.9),light,tail,[.06,.08,-.17]);tm.rotation.y=-.2;
 const desk=new THREE.Group();root.add(desk);desk.position.set(0,.503,.49);desk.visible=false;
 const tablet=mesh(boxGeom(1.58,.08,.48,.035),mint,desk);tablet.rotation.x=.12;mesh(boxGeom(1.40,.012,.36,.005),cream,desk,[0,.054,0]);for(let i=0;i<3;i++)mesh(boxGeom(.12,.02,.11,.008),light,desk,[-.64+i*.64,.072,-.09]);
 const sparks=[];for(let i=0;i<5;i++){const s=mesh(new THREE.OctahedronGeometry(.08,0),gold,root);s.visible=false;sparks.push(s)}
 const dust=new THREE.Group();root.add(dust);
 const stamp=mesh(new THREE.TorusGeometry(.8,.012,8,60),light,dust,[0,.018,0]);stamp.rotation.x=-Math.PI/2;stamp.material=mat(C.light);stamp.material.transparent=true;stamp.material.opacity=0;
 function apply(p){body.position.set(p.x,p.y,0);body.scale.set(p.sx,p.sy,p.sz);body.rotation.set(p.rx,p.ry,p.rz);headPivot.rotation.set(p.headX,p.headY,p.headZ);arms[0].rotation.set(p.armLX,0,-p.armL);arms[1].rotation.set(p.armRX,0,-p.armR);feet[0].position.y=.16+p.footL;feet[1].position.y=.16+p.footR;ears[0].rotation.z=.34+p.earL;ears[1].rotation.z=-.34+p.earR;
 eyes.forEach((eye,i)=>{eye.position.x=(i===0?-.245:.245)+p.gazeX*.038;eye.position.y=.28+p.gazeY*.039;eye.scale.y=Math.max(.055,p.blink*(i===1?p.wink:1))});mouthGroup.scale.set(1+p.smile*.18,1+p.smile*.25,1);openMouth.visible=p.mouthOpen>.18;openMouth.scale.y=Math.max(.15,p.mouthOpen);mouth.visible=p.mouthOpen<=.18;
 desk.visible=p.work>.015;desk.scale.setScalar(Math.max(.001,p.work));desk.position.y=.36+.143*p.work;tail.rotation.z=-p.rz*.8;tail.rotation.x=-p.y*.4;
 sparks.forEach((s,i)=>{s.visible=p.spark>.015;const a=i*Math.PI*2/5+.2;s.position.set(Math.cos(a)*(1.0+p.spark*.18),1.42+Math.sin(a)*.83+p.spark*.3,.12);s.scale.setScalar(p.spark*(.8+i*.08));s.rotation.set(p.spark*2+i,p.spark+i,Math.PI/4)});
 }
 function setDetail(hero){headPivot.scale.set(hero?1:1.065,1,1);ears.forEach(e=>e.scale.y=hero?1:.84);pin.visible=hero;leaf.visible=hero;}
 return {root,apply,setDetail,materials:{mint,cream,light},triangles:0};
}
export function makeStage(renderer){
 const scene=new THREE.Scene();
 scene.add(new THREE.HemisphereLight(0xfff8e7,0x708a7f,1.22));
 const key=new THREE.DirectionalLight(0xfff5e5,2.8);key.position.set(-3.2,5.6,4.5);key.castShadow=true;key.shadow.mapSize.set(1024,1024);key.shadow.camera.left=-3;key.shadow.camera.right=3;key.shadow.camera.top=5;key.shadow.camera.bottom=-1;key.shadow.normalBias=.024;key.shadow.bias=-.0001;key.shadow.radius=4;scene.add(key);
 const fill=new THREE.DirectionalLight(0xd0e9e7,.65);fill.position.set(3,2,2);scene.add(fill);const rim=new THREE.DirectionalLight(0xffedce,2.2);rim.position.set(1,4,-3);scene.add(rim);
 // Offline studio environment with broad luminous panels for tactile vinyl reflections.
 const studio=new THREE.Scene();studio.background=new THREE.Color(0x9aa99f);const panel=(w,h,pos)=>{let m=new THREE.Mesh(new THREE.PlaneGeometry(w,h),new THREE.MeshBasicMaterial({color:0xffffff,side:THREE.DoubleSide}));m.position.set(...pos);m.lookAt(0,1,0);studio.add(m)};panel(4,6,[-4,4,4]);panel(3,5,[4,3,2]);panel(4,3,[0,6,-3]);const pmrem=new THREE.PMREMGenerator(renderer);const env=pmrem.fromScene(studio,.08);scene.environment=env.texture;scene.environmentIntensity=.45;pmrem.dispose();studio.traverse(o=>{o.geometry?.dispose();o.material?.dispose()});
 const shadowCanvas=document.createElement('canvas');shadowCanvas.width=128;shadowCanvas.height=128;const ctx=shadowCanvas.getContext('2d');const g=ctx.createRadialGradient(64,64,0,64,64,64);g.addColorStop(0,'rgba(35,63,49,.30)');g.addColorStop(.4,'rgba(35,63,49,.19)');g.addColorStop(1,'rgba(35,63,49,0)');ctx.fillStyle=g;ctx.fillRect(0,0,128,128);const texture=new THREE.CanvasTexture(shadowCanvas);const contact=new THREE.Mesh(new THREE.PlaneGeometry(2.7,2.0),new THREE.MeshBasicMaterial({map:texture,transparent:true,depthWrite:false}));contact.rotation.x=-Math.PI/2;contact.position.set(0,.008,0);scene.add(contact);
 const ground=new THREE.Mesh(new THREE.PlaneGeometry(200,200),new THREE.ShadowMaterial({color:0x294f3b,opacity:.015}));ground.rotation.x=-Math.PI/2;ground.receiveShadow=false;ground.position.y=-.025;scene.add(ground);
 const model=makeCharacter();scene.add(model.root);return {scene,model,contact,env,key};
}
