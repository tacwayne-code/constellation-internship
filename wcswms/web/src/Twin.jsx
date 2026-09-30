import React,{useEffect,useRef,useState} from 'react';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {Maximize,RotateCcw} from 'lucide-react';

export default function Twin({state,onSelect}) {
  const host=useRef(null),latest=useRef(state),selected=useRef(onSelect),controlsRef=useRef(null);
  const [error,setError]=useState('');
  latest.current=state; selected.current=onSelect;
  useEffect(()=>{
    const el=host.current; let renderer;
    try { renderer=new THREE.WebGLRenderer({antialias:true,alpha:false}); }
    catch {setError('当前浏览器不支持 WebGL。库存和任务功能仍可使用。');return;}
    const scene=new THREE.Scene(); scene.background=new THREE.Color('#eef2f7');
    renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));renderer.shadowMap.enabled=true;
    renderer.shadowMap.type=THREE.PCFSoftShadowMap;el.appendChild(renderer.domElement);
    const camera=new THREE.PerspectiveCamera(39,1,.1,100);
    const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;
    controls.maxPolarAngle=Math.PI/2-.04;controls.minDistance=5;controls.maxDistance=26;
    const reset=()=>{camera.position.set(13,8.2,11.8);controls.target.set(3.7,1.9,0);controls.update();};reset();controlsRef.current=reset;
    scene.add(new THREE.HemisphereLight(0xffffff,0xa7b7ca,2.4));
    const light=new THREE.DirectionalLight(0xffffff,3);light.position.set(2,12,6);light.castShadow=true;light.shadow.mapSize.set(2048,2048);
    Object.assign(light.shadow.camera,{left:-12,right:12,top:12,bottom:-12});scene.add(light);
    const mat={steel:new THREE.MeshStandardMaterial({color:0x8395a9,metalness:.5,roughness:.45}),blue:new THREE.MeshStandardMaterial({color:0x215a91,metalness:.3,roughness:.5}),orange:new THREE.MeshStandardMaterial({color:0xf39a23,metalness:.25,roughness:.5}),dark:new THREE.MeshStandardMaterial({color:0x334155,metalness:.4,roughness:.5}),box:new THREE.MeshStandardMaterial({color:0x27b8c3,metalness:.1,roughness:.5})};
    const geometries=[],materials=[];
    function box(parent,x,y,z,w,h,d,material){const g=new THREE.BoxGeometry(w,h,d);geometries.push(g);const m=new THREE.Mesh(g,material);m.position.set(x,y,z);m.castShadow=true;m.receiveShadow=true;parent.add(m);return m;}
    const floorMat=new THREE.MeshStandardMaterial({color:0xe3e9f0,roughness:.95});materials.push(floorMat);
    box(scene,3.5,-.13,0,11,.2,7,floorMat);
    const grid=new THREE.GridHelper(12,24,0xc5cfdd,0xd5dee9);grid.position.set(3.5,-.025,0);scene.add(grid);
    const layout=latest.current.layout,slots=[],loadMeshes={};
    for(const side of layout.sides){const z=(side==='L'?-1:1)*layout.aisle_half_width_m;
      for(let c=0;c<=layout.columns;c++)for(const zz of [z-.43,z+.43]) box(scene,.6+c*layout.column_pitch_m,2,zz,.06,4.1,.06,mat.blue);
      for(let l=0;l<layout.levels;l++){
        const y=.28+l*layout.level_pitch_m;
        for(const zz of [z-.43,z+.43])box(scene,4.2,y,zz,7.26,.08,.07,mat.steel);
      }
      for(let c=1;c<=layout.columns;c++)for(let l=1;l<=layout.levels;l++){
        const id=`${side}-${String(c).padStart(2,'0')}-${String(l).padStart(2,'0')}`,x=c*layout.column_pitch_m,y=.4+(l-1)*layout.level_pitch_m;
        box(scene,x,y-.08,z,1.08,.045,.84,mat.steel);
        const sm=new THREE.MeshStandardMaterial({color:0x96adc5,transparent:true,opacity:.045,depthWrite:false});materials.push(sm);
        const cell=box(scene,x,y+.32,z,1.05,.7,.84,sm);cell.userData.location=id;slots.push(cell);
        const cargo=new THREE.Group();box(cargo,0,.26,0,.76,.48,.62,mat.box);box(cargo,0,.49,0,.81,.055,.67,mat.box);box(cargo,0,.02,0,.82,.05,.68,mat.dark);
        cargo.position.set(x,y,z);scene.add(cargo);loadMeshes[id]=cargo;
      }
    }
    for(const z of [-.28,.28])box(scene,3.6,.015,z,9,.06,.045,mat.steel);
    const crane=new THREE.Group();scene.add(crane);
    box(crane,0,.12,0,.68,.24,.75,mat.dark);
    for(const x of [-.22,.22])box(crane,x,2.3,0,.11,4.6,.19,mat.orange);
    box(crane,0,4.58,0,.65,.18,.38,mat.dark);
    const carriage=new THREE.Group();crane.add(carriage);box(carriage,0,0,0,.7,.16,.72,mat.orange);box(carriage,-.33,.2,0,.15,.4,.4,mat.dark);
    const fork=new THREE.Group();carriage.add(fork);for(const x of [-.2,.2])box(fork,x,.1,0,.07,.045,.8,mat.steel);
    const carried=new THREE.Group();box(carried,0,.38,0,.76,.48,.62,mat.box);box(carried,0,.63,0,.81,.05,.67,mat.box);fork.add(carried);
    for(const [id,z] of [['IN',-1.15],['OUT',1.15]]){
      box(scene,0,.18,z,1,.35,.9,mat.dark);for(let i=0;i<7;i++)box(scene,-.4+i*.13,.37,z,.05,.05,.86,mat.steel);
      const cargo=box(scene,0,.66,z,.76,.5,.62,mat.box);loadMeshes[id]=cargo;
    }
    const ray=new THREE.Raycaster(),pointer=new THREE.Vector2();let start;
    const down=e=>{start=[e.clientX,e.clientY];};
    const up=e=>{if(!start||Math.hypot(start[0]-e.clientX,start[1]-e.clientY)>5)return;const r=renderer.domElement.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(pointer,camera);const hit=ray.intersectObjects(slots)[0];if(hit)selected.current(hit.object.userData.location);};
    renderer.domElement.addEventListener('pointerdown',down);renderer.domElement.addEventListener('pointerup',up);
    const resize=new ResizeObserver(()=>{const {width,height}=el.getBoundingClientRect();renderer.setSize(width,height);camera.aspect=width/height;camera.updateProjectionMatrix();});resize.observe(el);
    let frame;
    function render(){const s=latest.current,d=s.device;crane.position.x=d.x;carriage.position.y=d.y;fork.position.z=d.z;carried.visible=!!d.carrying;
      for(const [id,m]of Object.entries(loadMeshes)){m.visible=!!(s.locations[id]||s.stations[id])?.load;}
      for(const m of slots){const p=s.locations[m.userData.location];m.material.color.set(p.reserved?0x2878ed:0x96adc5);m.material.opacity=p.reserved?.22:.025;}
      controls.update();renderer.render(scene,camera);frame=requestAnimationFrame(render);
    }render();
    return ()=>{cancelAnimationFrame(frame);resize.disconnect();controls.dispose();geometries.forEach(g=>g.dispose());Object.values(mat).forEach(m=>m.dispose());materials.forEach(m=>m.dispose());grid.geometry.dispose();grid.material.dispose();renderer.dispose();el.replaceChildren();};
  },[]);
  return <div className="twin"><div className="canvas-tools"><span>参数化模型 · 尺寸待标定</span><button title="重置视角" aria-label="重置视角" onClick={()=>controlsRef.current?.()}><RotateCcw size={15}/></button></div><div className="canvas" ref={host} role="img" aria-label="可旋转的堆垛机仓库三维仿真">{error&&<p className="canvas-error">{error}</p>}</div><div className="canvas-bottom"><span>拖动旋转 · 滚轮缩放 · 点击库位</span><div className="legend"><i/>空闲<i className="occupied"/>占用<i className="reserved"/>预留</div></div></div>;
}
