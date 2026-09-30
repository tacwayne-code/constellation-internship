import React, {useEffect, useRef, useState} from 'react';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {Crosshair, RotateCcw, Layers3} from 'lucide-react';
import './real-twin.css';
import {physicalPreviewPose} from './twin-pose';

const DEFAULT_URL = '/assets/structure/sample-room.glb';
const GROUP_LABEL = {static:'货架 / 地轨', travel:'X 行走机体', lift:'X + Y 载货台', fork_mid:'X + Y + Z/2 中级叉', fork_tip:'X + Y + Z 末级叉', design_load:'设计料箱'};
const finite = value => Number.isFinite(Number(value)) ? Number(value) : 0;

/** Every runtime pose comes from the caller's sampled state; there is no local motion simulation. */
export default function RealTwin({state, onSelect, url = DEFAULT_URL, physical = false, calibration = null}) {
  const host = useRef(null), latest = useRef(state), selectRef = useRef(onSelect), actions = useRef({});
  const calibrationRef = useRef(calibration);
  calibrationRef.current = calibration;
  const settings = useRef({hardware:!physical, rack:true, design:!state&&!physical, candidates:false});
  const [options, setOptions] = useState(settings.current);
  const [status, setStatus] = useState('加载完整 CAD 网格与映射…');
  const [mapping, setMapping] = useState(null), [selection, setSelection] = useState(null), [ready, setReady] = useState(false);
  latest.current = state; selectRef.current = onSelect;
  const toggle = key => setOptions(previous => {const next = {...previous, [key]:!previous[key]}; settings.current = next; return next;});

  useEffect(() => {
    const element = host.current;
    let renderer, disposed = false, frame, world, map, bounds, center, size, carried, candidateMesh;
    const abort = new AbortController(), geometries = new Set(), materials = new Set(), batches = [], operational = [];
    const motionGroups = Object.fromEntries(['static','travel','lift','fork_mid','fork_tip','design_load','unclassified'].map(key => [key,new THREE.Group()]));
    try {renderer = new THREE.WebGLRenderer({antialias:true});}
    catch {setStatus('WebGL 不可用，仍可查看映射清单。'); return undefined;}
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, physical ? 1 : 1.5));
    renderer.setClearColor('#edf2f7');
    element.appendChild(renderer.domElement);
    const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(38, 1, .02, 200);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8193ad, 3));
    const light = new THREE.DirectionalLight(0xffffff, 3); light.position.set(0,12,8); scene.add(light);
    const controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true; controls.maxPolarAngle = Math.PI*.92;
    camera.position.set(14,8,12);
    const trackGeometry = geometry => {geometries.add(geometry); return geometry;};
    const trackMaterial = material => {materials.add(material); return material;};
    const disposeGLTF = gltf => gltf.scene.traverse(object => {if(object.isMesh){object.geometry.dispose();[].concat(object.material).forEach(material=>material.dispose());}});
    const loadModel = new Promise((resolve,reject) => new GLTFLoader().load(url, resolve,
      progress => {if(!disposed && progress.total)setStatus(`加载完整 CAD 网格… ${Math.round(progress.loaded/progress.total*100)}%`);}, reject));
    const loadMap = fetch('/assets/structure/twin-map.json', {signal:abort.signal}).then(response => {
      if(!response.ok)throw new Error('映射清单不可用'); return response.json();
    });
    // Keep both outcomes observed, including when unmounted before the model download finishes.
    Promise.allSettled([loadModel,loadMap]).then(results => {
      const [modelResult,mapResult] = results;
      if(disposed){if(modelResult.status==='fulfilled')disposeGLTF(modelResult.value);return;}
      if(modelResult.status !== 'fulfilled' || mapResult.status !== 'fulfilled'){
        if(modelResult.status==='fulfilled')disposeGLTF(modelResult.value);
        setStatus('完整模型或映射清单加载失败，请刷新重试。'); return;
      }
      const gltf = modelResult.value; map = mapResult.value; setMapping(map);
      gltf.scene.updateMatrixWorld(true);
      bounds = new THREE.Box3().setFromObject(gltf.scene);
      center = bounds.getCenter(new THREE.Vector3()); size = bounds.getSize(new THREE.Vector3());
      world = new THREE.Group(); world.position.set(-center.x,-bounds.min.y,-center.z); scene.add(world);
      Object.values(motionGroups).forEach(group=>world.add(group));
      const grouped = new Map(); let tote;
      gltf.scene.traverse(object => {
        if(!object.isMesh)return;
        const data = object.userData, group = data.motion_group || 'unclassified';
        const key = [object.geometry.uuid, object.material.uuid, group, data.hardware].join('|');
        if(!grouped.has(key))grouped.set(key,{geometry:object.geometry,material:object.material,group,hardware:!!data.hardware,items:[]});
        grouped.get(key).items.push({matrix:object.matrixWorld.clone(),data});
        if(group==='design_load' && !tote)tote = object;
      });
      for(const batch of grouped.values()){
        const material = batch.material;
        // SolidWorks extraction provides positions only. Shared normals are computed once by GLTFLoader/Three.
        if(!batch.geometry.getAttribute('normal'))batch.geometry.computeVertexNormals();
        const mesh = new THREE.InstancedMesh(trackGeometry(batch.geometry), trackMaterial(material), batch.items.length);
        batch.items.forEach((item,index)=>mesh.setMatrixAt(index,item.matrix));
        mesh.instanceMatrix.needsUpdate = true; mesh.userData.batch = batch; mesh.computeBoundingSphere();
        motionGroups[batch.group].add(mesh); batches.push(mesh);
      }
      const candidateById = new Map(map.candidate_locations.map(candidate=>[candidate.id,candidate]));
      if(tote){
        const matrix = tote.matrixWorld.clone(), rotation = matrix.clone(); rotation.setPosition(0,0,0);
        const inventoryMaterial = trackMaterial(new THREE.MeshStandardMaterial({color:0x1dabb4,metalness:.1,roughness:.65}));
        const stockGroup = new THREE.Group(); world.add(stockGroup);
        for(const id of map.operational_location_ids){
          const location = candidateById.get(id), mesh = new THREE.Mesh(tote.geometry,inventoryMaterial);
          mesh.matrixAutoUpdate = false; mesh.matrix.copy(rotation); mesh.matrix.setPosition(...location.position_m);
          mesh.userData.location = id; stockGroup.add(mesh); operational.push(mesh);
        }
        // IN/OUT have no confirmed station CAD: only the live tote is shown at the virtual demo handoff.
        for(const [id,sign] of [['IN',-1],['OUT',1]]){
          const mesh = new THREE.Mesh(tote.geometry,inventoryMaterial), cfg=map.coordinate_mapping;
          mesh.matrixAutoUpdate=false; mesh.matrix.copy(rotation);
          mesh.matrix.setPosition(cfg.x.offset_m,cfg.y.offset_m+.4*cfg.y.scale,map.motion_reference_m.aisle_z+sign*.68);
          mesh.userData.location=id; mesh.userData.virtual_station=true; stockGroup.add(mesh); operational.push(mesh);
        }
        carried = new THREE.Mesh(tote.geometry, inventoryMaterial); carried.matrixAutoUpdate = false;
        carried.matrix.copy(rotation); world.add(carried);
      }
      const cellGeometry = trackGeometry(new THREE.BoxGeometry(.42,.29,.59));
      const cellMaterial = trackMaterial(new THREE.MeshBasicMaterial({color:0x7d95b0,wireframe:true,transparent:true,opacity:.28}));
      candidateMesh = new THREE.InstancedMesh(cellGeometry,cellMaterial,map.candidate_locations.length);
      map.candidate_locations.forEach((candidate,index)=>candidateMesh.setMatrixAt(index,new THREE.Matrix4().makeTranslation(candidate.position_m[0],candidate.position_m[1]+.13,candidate.position_m[2])));
      candidateMesh.instanceMatrix.needsUpdate = true; world.add(candidateMesh);
      const grid = new THREE.GridHelper(Math.ceil(size.x+3),Math.ceil(size.x+3)*2,0xbdcadb,0xd9e1ec);
      grid.position.y = -.012; scene.add(grid); trackGeometry(grid.geometry); [].concat(grid.material).forEach(trackMaterial);
      const reset = () => {const distance = Math.max(size.x,size.y,size.z)*.92; camera.position.set(distance*.62,size.y+distance*.39,distance*.82); controls.target.set(0,size.y*.43,0); controls.update();};
      actions.current = {reset, focus:() => {
        const d=!physical&&latest.current?.device, coordinate=map.coordinate_mapping;
        const pose=physical ? physicalPreviewPose(latest.current,map.motion_reference_m,calibrationRef.current) : null;
        const x=pose ? pose.x : d ? coordinate.x.offset_m+finite(d.x)*coordinate.x.scale : map.motion_reference_m.travel_x;
        const y=pose ? pose.y : d ? coordinate.y.offset_m+finite(d.y)*coordinate.y.scale : map.motion_reference_m.lift_y;
        const point = new THREE.Vector3(x-center.x,y-bounds.min.y,map.motion_reference_m.aisle_z-center.z);
        camera.position.copy(point).add(new THREE.Vector3(-2.8,1.8,3.6)); controls.target.copy(point); controls.update();
      }};
      reset(); setStatus(''); setReady(true);
    });
    const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2(); let pointerStart;
    const down = event => {pointerStart=[event.clientX,event.clientY];};
    const up = event => {
      if(!world || !pointerStart || Math.hypot(event.clientX-pointerStart[0],event.clientY-pointerStart[1])>5)return;
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);
      raycaster.setFromCamera(pointer,camera);
      const stock = raycaster.intersectObjects(operational.filter(mesh=>mesh.visible))[0];
      if(stock){setSelection({source_path:stock.object.userData.location,motion_group:'inventory'});selectRef.current?.(stock.object.userData.location);return;}
      if(settings.current.candidates){
        const candidate = raycaster.intersectObject(candidateMesh)[0];
        if(candidate){const location=map.candidate_locations[candidate.instanceId];setSelection({source_path:location.id+' · '+(location.evidence==='CAD_TOTE'?'设计料箱证据':'网格推断'),motion_group:'candidate'});if(map.operational_location_ids.includes(location.id))selectRef.current?.(location.id);return;}
      }
      const hit = raycaster.intersectObjects(batches.filter(mesh=>mesh.visible))[0];
      if(hit)setSelection(hit.object.userData.batch.items[hit.instanceId].data);
    };
    renderer.domElement.addEventListener('pointerdown',down); renderer.domElement.addEventListener('pointerup',up);
    const resize = new ResizeObserver(() => {
      const width=element.clientWidth,height=element.clientHeight;
      if(!width||!height)return;
      renderer.setSize(width,height);camera.aspect=width/height;camera.updateProjectionMatrix();
    }); resize.observe(element);
    let lastPaint=0;
    const render = time => {
      frame=requestAnimationFrame(render);
      // Detailed 11M-triangle CAD is capped at 30 FPS; feedback itself remains unmodified.
      if(time-lastPaint<32)return; lastPaint=time;
      if(map){
        const current=latest.current, d=!physical&&current?.device, cfg=map.coordinate_mapping, ref=map.motion_reference_m;
        const pose=physical ? physicalPreviewPose(current,ref,calibrationRef.current) : null;
        const x=pose ? pose.x : d ? cfg.x.offset_m+finite(d.x)*cfg.x.scale : ref.travel_x;
        const y=pose ? pose.y : d ? cfg.y.offset_m+finite(d.y)*cfg.y.scale : ref.lift_y;
        const z=d ? finite(d.z)*cfg.z.scale : 0;
        const dx=pose ? pose.dx : d ? x-ref.travel_x : 0, dy=pose ? pose.dy : d ? y-ref.lift_y : 0;
        motionGroups.travel.position.set(dx,0,0); motionGroups.lift.position.set(dx,dy,0);
        motionGroups.fork_mid.position.set(dx,dy,pose ? pose.forkMid : d ? z*.5-ref.fork_mid_extension : 0);
        motionGroups.fork_tip.position.set(dx,dy,pose ? pose.forkTip : d ? z-ref.fork_tip_extension : 0);
        for(const mesh of batches){const batch=mesh.userData.batch;mesh.visible=(!batch.hardware||settings.current.hardware)&&(batch.group!=='static'||settings.current.rack)&&(batch.group!=='design_load'||settings.current.design);}
        candidateMesh.visible=settings.current.candidates;
        for(const mesh of operational)mesh.visible=!!d&&!settings.current.design&&!!(current.locations?.[mesh.userData.location]||current.stations?.[mesh.userData.location])?.load;
        if(carried){carried.visible=!!d?.carrying&&!settings.current.design;carried.matrix.setPosition(x,y,ref.aisle_z+z);}
      }
      controls.update();renderer.render(scene,camera);
    };frame=requestAnimationFrame(render);
    return () => {disposed=true;abort.abort();cancelAnimationFrame(frame);resize.disconnect();controls.dispose();actions.current={};geometries.forEach(geometry=>geometry.dispose());materials.forEach(material=>material.dispose());renderer.dispose();element.replaceChildren();};
  }, [url,physical]);

  return <section className="real-twin">
    <div className="real-twin-toolbar">
      <span><Layers3 size={15}/>{physical?(calibration?'真实 CAD · 一层一列对齐':'真实 CAD · 相对位移预览'):state?'真实 CAD · 实时轴映射':'真实 CAD · 原始装配'}</span>
      <div><button className="compact" disabled={!ready} onClick={()=>actions.current.focus?.()}><Crosshair size={14}/>设备局部</button><button className="compact" disabled={!ready} onClick={()=>actions.current.reset?.()} aria-label="重置真实模型视角"><RotateCcw size={14}/>全景</button></div>
    </div>
    <div ref={host} className="real-twin-canvas" role="img" aria-label={physical?(calibration?'真机一层一列单点对齐，行走方向已反转':'真机坐标相对位移预览，绝对位置未标定'):'包含完整原始装配零件并绑定实时轴反馈的仓库数字孪生'}/>
    {status && <div className="real-twin-loading" role="status">{status}</div>}
    <div className="real-twin-options">
      {Object.entries({hardware:'完整五金件',rack:'货架与地轨',design:'图纸料箱（占位）',...(!physical?{candidates:'候选库位'}:{})}).map(([key,label])=><label key={key}><input type="checkbox" checked={options[key]} onChange={()=>toggle(key)}/>{label}</label>)}
      <span>拖动旋转 · 双指 / 滚轮缩放 · 点击零件追溯</span>
    </div>
    {selection && <div className="real-twin-selection"><strong>{GROUP_LABEL[selection.motion_group]|| (selection.motion_group==='inventory'?'实时库存':'库位候选')}</strong><span>{selection.source_path}</span>{selection.source_file&&<small>{selection.source_file}</small>}<button onClick={()=>setSelection(null)} aria-label="关闭零件详情">×</button></div>}
    <div className="real-twin-caption">
      <span>{mapping ? `${mapping.coverage.rendered_instances.toLocaleString()} 实例 · ${mapping.coverage.part_files} 零件 · 缺失引用 ${mapping.coverage.missing_references}`:'读取装配覆盖率…'}</span>
      <strong>{physical?(calibration?'单点对齐 · 行走方向已确认':'相对位移 · 绝对零点 / 方向待标定'):'运动组 / 坐标比例待标定'}</strong>
    </div>
    {mapping && <details className="real-twin-details"><summary>查看映射范围与校准依据</summary>
      <p>原始三角网格保持真实尺寸。{physical?(calibration?'按已保存的一层一列坐标映射：现场第一列从 CAD 的 X 最大端开始，行走正向映射为 CAD X 负向；第一层对齐最低层料箱底面。货叉 0 mm 对应现场中间位置，模型已扣除原装配中级叉 0.34 m、末级叉 0.68 m 的伸出量。后台重启不会改变基准。升降和货叉方向、双级联动比例及其他层列仍需现场核对。断连时保留最后位置。':'画面仅叠加 DB5 三轴相对首个有效采样的位移，毫米转换为米；方向和货叉比例需现场标定，服务器重启后重新取基准。'):state?'实时画面使用 PLC 状态提供的 X / Y / Z，经演示比例映射到 CAD；页面不独立推演轴运动。设计料箱仅代表图纸中的占位，开启后替代实时库存显示。':'此视图保留原装配位置，可在工作台查看实时绑定。设计料箱仅代表图纸占位。'}</p>
      <div>{mapping.motion_groups.map(group=><span key={group.id}>{group.label}<b>{mapping.coverage.group_counts[group.id]||0}</b></span>)}</div>
      {!physical&&<p>设计候选：{mapping.coverage.candidate_locations}，其中 {mapping.coverage.observed_location_count} 个有料箱坐标证据，其余由规则网格补全；当前任务绑定 {mapping.coverage.operational_demo_locations} 个演示位。候选位均未获现场确认。原图纸 {mapping.coverage.observed_load_instances} 个料箱实例中有 {mapping.coverage.duplicate_design_load_instances} 个坐标重复，完整模型保留原样。</p>}
      <p>缺失：{mapping.missing_components.join('；') || '无'}。传动链变形、碰撞包络及传感器位置尚待标定。{physical?'只读展示不使用演示库存和虚拟站台。':'IN/OUT 仅显示虚拟交接点的实时料箱，尚无已确认站台模型。'}</p>
      <a href="/assets/structure/instance-map.json" target="_blank" rel="noreferrer">逐实例图纸映射清单</a><a href="/assets/structure/twin-map.json" target="_blank" rel="noreferrer">坐标与运动组配置</a>
      {physical&&calibration&&<a href="/assets/structure/physical-calibration.json" target="_blank" rel="noreferrer">现场固定显示基准</a>}
    </details>}
  </section>;
}
