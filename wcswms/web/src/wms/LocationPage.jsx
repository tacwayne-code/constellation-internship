import React,{useMemo,useState} from 'react';
import {Grid2X2,List,Plus,Settings2} from 'lucide-react';
import {api,requestId,locationLabel,kindNames,displayLocation,depthName,boxName} from './api';
import {Empty,Modal,MaterialRows,Badge} from './components';
import {AddLocation} from './WarehousePages';
import {allocationSummary} from './allocation';
import AllocationSettings from './AllocationSettings';
import Pager from './Pager';
import {usePagination,useTableSize,useViewport} from './paging';
import './locations.css';

const labels={EMPTY:'可用空位',BLOCKED:'通道待清空',UNVERIFIED:'待核对',OCCUPIED:'已占用',RESERVED:'任务预留',DISABLED:'已停用',UNREGISTERED:'未建档'};
const slotId=(side,level,column,depth)=>`${side===1?'L':'R'}-${String(column).padStart(2,'0')}-${String(level).padStart(2,'0')}-${depth}`;
const sideName=side=>side===1?'左仓':'右仓';

export default function LocationPage({state,canEdit,refresh,notify}){
 const [side,setSide]=useState(1),[view,setView]=useState('map'),[settings,setSettings]=useState(false),[rules,setRules]=useState(false),[adding,setAdding]=useState(false),[selected,setSelected]=useState(null);
 const [columnPage,setColumnPage]=useState(0),[levelPage,setLevelPage]=useState(0);
 const locations=useMemo(()=>new Map(state.locations.map(item=>[item.id,item])),[state.locations]);
 const stock=useMemo(()=>new Map(state.stock.filter(item=>item.status==='IN_STOCK').map(item=>[item.location,item])),[state.stock]);
 const tasks=useMemo(()=>new Map(state.tasks.map(item=>[item.id,item])),[state.tasks]);
 const sideLocations=state.locations.filter(item=>item.side===side),enabled=sideLocations.filter(item=>item.enabled);
 const layout=state.rack_layouts?.find(item=>item.side===side)||{side,levels:Math.max(0,...enabled.map(item=>item.level)),columns:Math.max(0,...enabled.map(item=>item.column)),depths:Math.max(1,...enabled.map(item=>item.depth)),revision:0};
 const depths=layout.depths===2?[1,2]:[1];
 const {width,height}=useViewport();
 const pageColumns=Math.max(2,Math.min(15,Math.floor((width-(width<760?90:width<1200?230:260))/72)));
 const pageLevels=Math.max(1,Math.min(10,Math.floor((height-(width<760?420:350))/(depths.length===2?118:62))));
 const listPaging=usePagination(sideLocations.slice().sort((a,b)=>b.level-a.level||a.column-b.column),Math.max(3,useTableSize()-2),side);
 const within=sideLocations.filter(item=>item.level<=layout.levels&&item.column<=layout.columns&&depths.includes(item.depth));
 const counts=Object.fromEntries(Object.keys(labels).map(status=>[status,within.filter(item=>item.status===status).length]));
 counts.UNREGISTERED=layout.levels*layout.columns*depths.length-within.length;
 const colStart=Math.min(columnPage,Math.max(0,Math.ceil(layout.columns/pageColumns)-1))*pageColumns+1;
 const levelStart=Math.min(levelPage,Math.max(0,Math.ceil(layout.levels/pageLevels)-1))*pageLevels+1;
 const colEnd=Math.min(layout.columns,colStart+pageColumns-1),levelEnd=Math.min(layout.levels,levelStart+pageLevels-1);
 const cols=Array.from({length:Math.max(0,colEnd-colStart+1)},(_,index)=>colStart+index);
 const levels=Array.from({length:Math.max(0,levelEnd-levelStart+1)},(_,index)=>levelEnd-index);
 function chooseSide(value){setSide(value);setColumnPage(0);setLevelPage(0);}
 const selectedLocation=selected&&(locations.get(selected.id)||{...selected,status:'UNREGISTERED'});
 async function changed(message){await refresh();notify(message);}
 return <>
  <div className="wm-section-tools">
   <div className="wm-tabs" aria-label="仓位侧">{[1,2].map(value=><button key={value} aria-pressed={side===value} className={side===value?'selected':''} onClick={()=>chooseSide(value)}>{sideName(value)}</button>)}</div>
   <div className="wm-tool-buttons"><button disabled={!canEdit} onClick={()=>setRules(true)}><Settings2 size={20}/>分配规则</button><button disabled={!canEdit} onClick={()=>setAdding(true)}><Plus size={20}/>新增层列位置</button><button className="wm-primary" disabled={!canEdit} onClick={()=>setSettings(true)}><Settings2 size={20}/>设置总层列数</button></div>
  </div>
  <section className="wm-panel wm-rack-panel">
   <div className="wm-rack-heading"><div><h2>{sideName(side)}库位平面图</h2><p className="wm-help">{layout.levels?`${layout.levels} 层 × ${layout.columns} 列 · ${depthName(depths.length)}结构 · ${layout.levels*layout.columns*depths.length} 个库位`:'尚未设置总层列数'} · 编号：仓侧-层-列 · 点击库位查看物料及更改状态</p></div><div className="wm-tabs wm-view-tabs"><button aria-pressed={view==='map'} className={view==='map'?'selected':''} onClick={()=>setView('map')}><Grid2X2 size={18}/>平面图</button><button aria-pressed={view==='list'} className={view==='list'?'selected':''} onClick={()=>setView('list')}><List size={18}/>列表</button></div></div>
   <p className="wm-allocation-summary">自动分配：{allocationSummary(state.allocation_policy)}</p>
   <div className="wm-rack-legend">{['OCCUPIED','RESERVED','EMPTY','BLOCKED','UNVERIFIED','DISABLED','UNREGISTERED'].map(status=><span key={status}><i className={`wm-slot-${status.toLowerCase()}`}/>{labels[status]} <b>{counts[status]}</b></span>)}</div>
   {view==='map'?layout.levels&&layout.columns?<>
    {(layout.columns>pageColumns||layout.levels>pageLevels)&&<div className="wm-rack-paging"><span>当前：{levelStart}—{levelEnd} 层 · {colStart}—{colEnd} 列</span><div><button disabled={colStart===1} onClick={()=>setColumnPage(Math.max(0,(colStart-1)/pageColumns-1))}>前 {pageColumns} 列</button><button disabled={colEnd===layout.columns} onClick={()=>setColumnPage((colStart-1)/pageColumns+1)}>后 {pageColumns} 列</button><button disabled={levelStart===1} onClick={()=>setLevelPage(Math.max(0,(levelStart-1)/pageLevels-1))}>较低楼层</button><button disabled={levelEnd===layout.levels} onClick={()=>setLevelPage((levelStart-1)/pageLevels+1)}>较高楼层</button></div></div>}
    <div className="wm-rack-scroll" role="region" aria-label={`${sideName(side)}层列库位图`}><div className="wm-rack-grid" style={{gridTemplateColumns:`30px repeat(${cols.length}, minmax(0, 1fr))`,gridTemplateRows:`24px repeat(${levels.length},minmax(0,1fr))`}}>
     <span className="wm-rack-axis">层 / 列</span>{cols.map(column=><span key={`col-${column}`} className="wm-rack-axis">{column} 列</span>)}
     {levels.map(level=><React.Fragment key={level}><span className="wm-rack-axis wm-level-axis">{level} 层</span>{cols.map(column=><div className="wm-rack-cell" key={column}>{depths.map(depth=>{
      const id=slotId(side,level,column,depth),location=locations.get(id),status=location?.status||'UNREGISTERED',item=stock.get(id),task=tasks.get(location?.reserved);
      return <button key={id} className={`wm-rack-slot wm-slot-${status.toLowerCase()}`} onClick={()=>setSelected({id,side,level,column,depth})} aria-label={`${sideName(side)} ${depthName(depth)} ${level}层 ${column}列 ${labels[status]} ${item?.material_name||item?.barcode||task?.barcode||''}`}><strong>{level}层 {column}列</strong>{depths.length===2&&<span className="wm-slot-depth">{depth===1?'单伸 · 前排':'双伸 · 后排'}</span>}<span className="wm-slot-status">{labels[status]}</span><small title={item?.material_name||item?.barcode||task?.barcode||displayLocation(id)}>{item?.material_name||item?.barcode||task?.barcode||displayLocation(id)}</small></button>;
     })}</div>)}</React.Fragment>)}
    </div></div><p className="wm-help wm-map-note">高层在上，列号从左向右增加。超出当前视图时切换层列；编号与 PLC 映射保持不变。</p>
   </>:<Empty title="尚未设置货架范围">解锁后点击“设置总层列数”，批量建立实际库位。</Empty>:
   <><div className="wm-table-wrap"><table className="wm-location-table"><thead><tr>{['库位编号','现场位置','状态','料箱','操作'].map(label=><th key={label}>{label}</th>)}</tr></thead><tbody>{listPaging.items.map(item=><tr key={item.id} className="wm-location-row" onClick={()=>setSelected(item)}><td><strong>{displayLocation(item.id)}</strong></td><td>{locationLabel(item)}</td><td>{labels[item.status]}</td><td>{item.barcode||'—'}</td><td><button aria-label={`管理库位 ${displayLocation(item.id)}`} onClick={()=>setSelected(item)}>查看 / 管理</button></td></tr>)}</tbody></table>{!sideLocations.length&&<Empty title="暂无库位记录"/>}</div><Pager paging={listPaging} label="库位"/></>}
  </section>
  {settings&&<RackSettings layout={layout} onClose={()=>setSettings(false)} onDone={async()=>{setColumnPage(0);setLevelPage(0);await changed('总层列数已保存');}}/>}
  {rules&&<AllocationSettings policy={state.allocation_policy} onClose={()=>setRules(false)} onDone={()=>changed('分配规则已保存，对新建任务立即生效')}/>}
  {adding&&<AddLocation state={state} side={side} onClose={()=>setAdding(false)} onDone={()=>changed('库位已新增，平面图范围已更新')}/>}
  {selectedLocation&&<LocationDetails key={selectedLocation.id} location={selectedLocation} stock={stock.get(selectedLocation.id)} task={tasks.get(selectedLocation.reserved)} canEdit={canEdit} onClose={()=>setSelected(null)} onSettings={()=>{setSelected(null);setSettings(true);}} onChanged={changed}/>}
 </>;
}

function RackSettings({layout,onClose,onDone}){
 const [form,setForm]=useState(()=>({request_id:requestId(),side:layout.side,levels:layout.levels||1,columns:layout.columns||1,depths:layout.depths||1,expected_revision:layout.revision,verified_empty:false})),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const update=(key,value)=>setForm(old=>({...old,[key]:value,request_id:requestId()}));
 async function submit(event){event.preventDefault();if(busy)return;setBusy(true);try{await api('/api/locations/layout',{...form,levels:Number(form.levels),columns:Number(form.columns),depths:Number(form.depths)});await onDone();onClose();}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <Modal title={`${sideName(layout.side)}总层列数`} onClose={onClose} className="wm-rack-settings"><form onSubmit={submit}>
  <div className="wm-form-grid"><label>库位结构<select disabled={busy} value={form.depths} onChange={e=>update('depths',Number(e.target.value))}><option value={1}>单伸</option><option value={2}>双伸（前排单伸 + 后排双伸）</option></select></label><label>总层数<input type="number" min="1" max="99" step="1" required disabled={busy} value={form.levels} onChange={e=>update('levels',e.target.value)}/></label><label>总列数<input type="number" min="1" max="99" step="1" required disabled={busy} value={form.columns} onChange={e=>update('columns',e.target.value)}/></label></div>
  <p className="wm-review">库位合计：{Number(form.levels)*Number(form.columns)*Number(form.depths)||0} 个<br/>当前：{layout.levels||0} 层 × {layout.columns||0} 列</p>
  <p className="wm-help">保存后按层列及伸位补齐库位。双伸后排作业需前排已核对且无占用、预留。已有库存和预留保持原位；缩小范围时，有库存或未结束任务的库位不能停用。</p>
  <label className="wm-check"><input type="checkbox" disabled={busy} checked={form.verified_empty} onChange={e=>update('verified_empty',e.target.checked)}/>已现场核对新增及重新启用的库位均为空</label><p className="wm-help">不勾选时，新建及随范围恢复的库位为“待核对”。单独停用的库位继续保持停用。</p>
  {error&&<p className="wm-error" role="alert">{error}</p>}<button className="wm-primary wm-full" disabled={busy}>{busy?'保存中…':'保存总层列数'}</button>
 </form></Modal>;
}

const actions={RELEASE:'解除占用',DISABLE:'停用库位',ENABLE:'重新启用'};
function LocationDetails({location,stock,task,canEdit,onClose,onSettings,onChanged}){
 const [confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[operation,setOperation]=useState(null);
 const incoming=!stock&&task?.kind==='INBOUND'?task:null,item=stock||incoming;
 const blocked=Boolean(location.reserved||location.blocking_tasks?.length||stock?.reserved);
 const stale=operation&&operation.expected_token!==location.management_token;
 function selectAction(action){setError('');setOperation({action,request_id:requestId(),expected_token:location.management_token,note:'',confirmed:false,stock});}
 function updateOperation(key,value){setOperation(previous=>({...previous,[key]:value,request_id:requestId()}));}
 async function verify(){if(busy||!confirmed||!canEdit||blocked)return;setBusy(true);try{await api(`/api/locations/${location.id}/verify-empty`,{confirmed:true});setConfirmed(false);await onChanged('已登记为可用空位');}catch(e){setError(e.message);}finally{setBusy(false);}}
 async function manage(event){
  event.preventDefault();if(busy||!canEdit||blocked||stale||!operation.confirmed)return;
  setBusy(true);setError('');
  try{const {stock:_,...body}=operation;await api(`/api/locations/${location.id}/state`,body);setOperation(null);setConfirmed(false);await onChanged(`${displayLocation(location.id)} 已${operation.action==='RELEASE'?'解除占用':operation.action==='DISABLE'?'停用':'重新启用'}`);}
  catch(e){setError(e.message);}finally{setBusy(false);}
 }
 return <Modal title="库位物料信息" onClose={onClose}><p className="wm-review"><b>{locationLabel(location)}</b><br/>{displayLocation(location.id)} · {labels[location.status]}</p>
  {!operation&&(item?<><h3>{incoming?'待入库物料（任务预留）':'当前在库物料'}</h3><dl className="wm-info-list wm-detail-grid"><div><dt>料箱条码</dt><dd>{item.barcode}</dd></div><div><dt>料箱类型</dt><dd>{boxName(item)}</dd></div>{item.container_type!=='EMPTY_BIN'&&<><div><dt>物料编码</dt><dd>{item.sku}</dd></div><MaterialRows item={item}/><div><dt>数量</dt><dd>{item.quantity}</dd></div><div><dt>批次</dt><dd>{item.batch||'—'}</dd></div></>}</dl></>:<p className="wm-help">{location.status==='UNREGISTERED'?'该位置尚未建档，设置总层列数后可批量生成。':location.status==='UNVERIFIED'?'该库位尚未现场核对，当前没有登记库存。':location.status==='DISABLED'?'该库位已停用，不参与出入库分配。':'该库位当前没有登记库存。'}</p>)}
  {location.access_error&&<p className="wm-help">{location.access_error}</p>}{task&&<div className="wm-location-task"><span>{kindNames[task.kind]} · {task.number}</span><Badge status={task.status}/></div>}
  {location.status==='DISABLED'&&stock&&<p className="wm-help">库位已停用，库存保留；重新启用后才能出库。</p>}
  {blocked&&<p className="wm-help">该库位关联未结束任务或预留{location.blocking_tasks?.length?`：${location.blocking_tasks.join('、')}`:''}。请先在任务页处理，再更改库位状态。</p>}
  {location.status==='UNVERIFIED'&&!operation&&<><label className="wm-check"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)} disabled={!canEdit||blocked}/>已在现场核对该库位没有料箱</label><button className="wm-primary wm-full" disabled={!canEdit||!confirmed||busy||blocked} onClick={verify}>核对为空</button></>}
  {location.status!=='UNREGISTERED'&&!operation&&<div className="wm-location-actions"><h3>库位状态管理</h3><div>{stock&&<button className="wm-danger" disabled={!canEdit||busy||blocked} onClick={()=>selectAction('RELEASE')}>解除占用</button>}<button disabled={!canEdit||busy||blocked} onClick={()=>selectAction(location.enabled?'DISABLE':'ENABLE')}>{location.enabled?'停用库位':'重新启用'}</button></div><p className="wm-help">停用后不参与出入库分配；解除占用会调整库存账目，请按现场情况操作。</p></div>}
  {operation&&<form className="wm-location-operation" onSubmit={manage}><h3>{actions[operation.action]}</h3>
   <p className="wm-help">{operation.action==='RELEASE'?`料箱 ${operation.stock.barcode} · ${operation.stock.sku} · 数量 ${operation.stock.quantity}。确认后从在库库存移出，保留调整记录和物料档案。若库位已停用，解除后仍保持停用。`:operation.action==='DISABLE'?'停止向该库位入库及从该库位出库，保留已有库存。总层列设置不会自动取消本次停用。':stock?'重新启用后，可对当前库存创建出库任务。':'重新启用后先列为待核对，核对为空后才能分配入库。'}</p>
   <label>操作原因<input value={operation.note} onChange={e=>updateOperation('note',e.target.value)} minLength={2} maxLength={200} required disabled={busy} placeholder="例如：设备检修、料箱已人工移走"/></label>
   <label className="wm-check"><input type="checkbox" checked={operation.confirmed} onChange={e=>updateOperation('confirmed',e.target.checked)} disabled={busy}/>{operation.action==='RELEASE'?'已现场确认料箱已移走，库位为空，并同意调整库存':'已核对该库位，可以执行本次状态变更'}</label>
   {stale&&<p className="wm-error" role="alert">库位或库存已变化，请取消本次操作后重新选择。</p>}
   <div className="wm-location-operation-buttons"><button type="button" disabled={busy} onClick={()=>{setOperation(null);setError('');}}>取消操作</button><button className={operation.action==='RELEASE'?'wm-danger':'wm-primary'} disabled={!canEdit||busy||blocked||stale||!operation.confirmed||operation.note.trim().length<2}>{busy?'保存中…':`确认${actions[operation.action]}`}</button></div>
  </form>}
  {location.status==='UNREGISTERED'&&!operation&&<button className="wm-primary wm-full wm-spaced" disabled={!canEdit} onClick={onSettings}>设置总层列数</button>}
  {error&&<p className="wm-error" role="alert">{error}</p>}
 </Modal>;
}
