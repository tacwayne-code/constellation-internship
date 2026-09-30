import React,{useEffect,useRef,useState} from 'react';
import {Barcode,ScanLine,Plus,Info} from 'lucide-react';
import {api,requestId,activeTask,kindNames,locationLabel,stockNames,storedLocation,taskLabel,boxName} from './api';
import {Badge,Empty,PlcSummary,TaskTable,ConfirmDialog,MaterialFields,MaterialRows,ScanHint} from './components';
import {materialDetails} from './material';
import useMaterialLookup from './useMaterialLookup';
import OutboundChoices from './OutboundChoices';
import {TaskButtons,TaskActionDialog} from './TaskActions';

export default function ScanPage({kind,state,canEdit,refresh,notify,onPage,emptyOnly=false}){
 const inbound=kind==='INBOUND',input=useRef(null),busyRef=useRef(false),scanTimer=useRef(null),scanRequest=useRef(null);
 const [form,setForm]=useState({barcode:'',sku:'',...materialDetails(),quantity:1,batch:'',location:'AUTO',container_type:emptyOnly?'EMPTY_BIN':'MATERIAL'}),[lookup,setLookup]=useState(null),[busy,setBusy]=useState(false),[handover,setHandover]=useState(false);
 const [reading,setReading]=useState(false);
 const [formId,setFormId]=useState(requestId),[scanMessage,setScanMessage]=useState('');
 const [action,setAction]=useState(null);
 const material=useMaterialLookup(setForm);
 const empty=form.container_type==='EMPTY_BIN';
 const draftKey=`wms-task-draft-${kind}${emptyOnly?'-empty':''}`;
 const [draft,setDraft]=useState(()=>{try{return JSON.parse(sessionStorage.getItem(draftKey))||{request_id:requestId(),tasks:[]};}catch{return {request_id:requestId(),tasks:[]};}});
 useEffect(()=>{sessionStorage.setItem(draftKey,JSON.stringify(draft));},[draft,draftKey]);
 function resetForm(){material.accept('',null);setForm(previous=>({...previous,barcode:'',sku:'',...materialDetails(),quantity:1,batch:'',location:'AUTO'}));setLookup(null);setScanMessage('');setFormId(requestId());input.current?.focus();}
 function chooseType(container_type){clearTimeout(scanTimer.current);scanRequest.current?.controller.abort();scanRequest.current=null;setReading(false);material.accept('',null);setForm(previous=>({...previous,container_type,barcode:'',sku:'',...materialDetails(),quantity:1,batch:''}));setLookup(null);setScanMessage('');setFormId(requestId());input.current?.focus();}
 async function submitDraft(){
  if(busyRef.current||!canEdit||!draft.tasks.length)return;
  busyRef.current=true;setBusy(true);
  try{const result=await api('/api/tasks/batch',draft);await refresh();setDraft({request_id:requestId(),tasks:[]});notify(`已登记 ${result.count} 条任务，请逐条核对下发`);}
  catch(error){notify(error.message,'error');}finally{busyRef.current=false;setBusy(false);}
 }

 useEffect(()=>()=>{clearTimeout(scanTimer.current);scanRequest.current?.controller.abort();scanRequest.current=null;},[]);
 const update=(key,value)=>{
  setFormId(requestId());
  if(key==='sku'){
   clearTimeout(scanTimer.current);scanRequest.current?.controller.abort();scanRequest.current=null;setReading(false);setScanMessage('');
   material.change(value);return;
  }
  setForm(previous=>({...previous,[key]:value}));
  if(key==='barcode'){
   clearTimeout(scanTimer.current);scanRequest.current?.controller.abort();scanRequest.current=null;
   setLookup(null);setScanMessage('');setReading(false);
   if(inbound)material.accept('',null);
   // HID scanners may have no Enter suffix. Query once input has settled;
   // Enter still queries immediately, cancelling this pending timer.
   if(value.trim())scanTimer.current=setTimeout(()=>scan(value),250);
  }
 };
 async function scan(value=form.barcode){
  clearTimeout(scanTimer.current);
  const barcode=value.trim();if(!barcode||busyRef.current||scanRequest.current?.barcode===barcode)return;
  scanRequest.current?.controller.abort();
  const request={barcode,controller:new AbortController()};scanRequest.current=request;
  setReading(true);setLookup(null);setScanMessage(inbound?'正在读取条码…':'正在查询库存…');
  try{
   const result=await api(`/api/lookup?barcode=${encodeURIComponent(barcode)}`,undefined,{signal:request.controller.signal});
   if(scanRequest.current!==request)return;
   setLookup(result);
   if(inbound&&!empty){
    const details=result.material||result.stock;
    material.accept(details?.sku||'',details);
    setFormId(requestId());
   }
   setScanMessage(result.task?`已有任务 ${result.task.number}，请勿重复建单`:result.stock?`${stockNames[result.stock.status]} · ${storedLocation(result.stock.location)}`:inbound&&empty?'空箱条码已读取，请选择库位':inbound&&result.material?'已识别物料编码，请核对资料与目标库位':inbound?'新料箱，请扫描或填写物料编码并选择目标库位':'未匹配完整条码，请从下方搜索结果选择料箱');
  }catch(error){if(scanRequest.current===request&&error.name!=='AbortError'){notify(error.message,'error');setScanMessage('条码查询失败，请重试');}}
  finally{if(scanRequest.current===request){scanRequest.current=null;setReading(false);}}
 }
 async function create(event){
  event.preventDefault();if(busyRef.current||reading||(!empty&&(material.pending||material.error))||!canEdit||(!inbound&&!validOut))return;
  clearTimeout(scanTimer.current);
  const row={request_id:formId,kind,...form,barcode:form.barcode.trim(),quantity:empty?0:Number(form.quantity)};
  if(draft.tasks.some(item=>item.barcode===row.barcode)){notify('本次清单已有该条码，请勿重复添加','error');return;}
  if(event.nativeEvent.submitter?.value==='draft'){
   if(draft.tasks.length>=50){notify('每次清单最多 50 条，请先提交','error');return;}
   setDraft(previous=>({request_id:requestId(),tasks:[...previous.tasks,row]}));resetForm();return;
  }
  busyRef.current=true;setBusy(true);
  try{
   const task=await api('/api/tasks',row);
   notify(`${task.number} 已登记，请在待办任务核对并下发`);await refresh();
   resetForm();
  }catch(error){notify(error.message,'error');}finally{busyRef.current=false;setBusy(false);}
 }
 const pending=state.tasks.filter(task=>!task.order_id&&activeTask(task)).slice().reverse();
 const outItem=lookup?.stock?(state.stock.find(item=>item.barcode===lookup.barcode)||lookup.stock):null;
 const outTask=state.tasks.find(task=>activeTask(task)&&task.barcode===outItem?.barcode);
 const emptyLocations=state.locations.filter(location=>location.status==='EMPTY'&&!location.inbound_access_error&&!draft.tasks.some(row=>row.location===location.id));
 const outLocation=state.locations.find(location=>location.id===outItem?.location);
 const validOut=outItem?.status==='IN_STOCK'&&!outItem.reserved&&!outLocation?.reserved&&!outTask&&outLocation?.enabled&&(outItem.container_type||'MATERIAL')===form.container_type&&lookup.barcode===form.barcode.trim();
 function selectStock(item){update('barcode',item.barcode);scan(item.barcode);}
 return <><div className="wm-workspace"><section className="wm-panel wm-registration"><h2>{inbound?'入库登记':'出库登记'}</h2><form onSubmit={create}>
  {!emptyOnly&&<div className="wm-tabs wm-type-picker" aria-label="料箱类型">{[['MATERIAL','物料箱'],['EMPTY_BIN','空箱']].map(([value,label])=><button type="button" key={value} aria-pressed={form.container_type===value} className={form.container_type===value?'selected':''} disabled={busy} onClick={()=>chooseType(value)}>{label}{inbound?'入库':'出库'}</button>)}</div>}
  <label>{inbound?'料箱条码':'料箱条码 / 物料搜索'}<div className="wm-scan-row"><div className="wm-scan-input"><Barcode size={26}/><input ref={input} aria-label={inbound?'料箱条码':'扫码或搜索出库库存'} autoFocus value={form.barcode} disabled={busy} onChange={event=>update('barcode',event.target.value)} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();scan(event.currentTarget.value);}}} placeholder={inbound?'扫码枪扫描或手动输入':'输入条码、编码、名称、型号或规格'} autoComplete="off" spellCheck={false} maxLength={inbound?64:200} required/></div><button type="button" className="wm-primary" onClick={()=>scan()} disabled={!form.barcode.trim()||busy||reading}><ScanLine size={24}/>{reading?'读取中…':inbound?'读取条码':'查询库存'}</button></div></label>
  <ScanHint value={form.barcode}/><p className="wm-help">{empty?'每个空箱使用独立条码，不需要物料编码、名称、型号或规格。':inbound?'扫码自动识别已有料箱或物料编码；新料箱可在下方扫描物料编码。数量、批次按本次填写。':'扫码自动带出库存；也可输入条码、编码、名称、型号、规格或批次，空格可组合多个关键词，再选择料箱。'}</p>
  {scanMessage&&<p className="wm-scan-result" role="status">{scanMessage}</p>}
  {!inbound&&!outItem&&<OutboundChoices query={form.barcode} state={state} busy={busy} onSelect={selectStock} containerType={form.container_type} excluded={draft.tasks.map(row=>row.barcode)}/>}
  {inbound?<div className="wm-form-grid">{!empty&&<><label>物料编码<input value={form.sku} placeholder="扫码或输入物料编码，自动带出资料" disabled={busy} onChange={event=>update('sku',event.target.value)} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();material.lookup(event.currentTarget.value);}}} autoComplete="off" spellCheck={false} maxLength={80} required/>{material.message&&<span className={material.error?'wm-error':'wm-help'} role="status">{material.message}</span>}</label><MaterialFields value={form} onChange={update} disabled={busy||reading||material.pending||material.error}/><label>数量<input type="number" min="1" max="1000000" step="1" inputMode="numeric" value={form.quantity} onChange={event=>update('quantity',event.target.value)} required/></label><label>批次<input value={form.batch} placeholder="选填" onChange={event=>update('batch',event.target.value)} maxLength={80}/></label></>}<label>目标库位<select value={form.location} onChange={event=>update('location',event.target.value)}><option value="AUTO">自动推荐</option>{emptyLocations.map(location=><option key={location.id} value={location.id}>{locationLabel(location)}</option>)}</select><span className="wm-help">伸位按库位设置自动带入</span></label><label>入口<select aria-label="入口" value={state.docks.inbound} readOnly onChange={()=>{}}><option value={state.docks.inbound}>{state.docks.inbound} 号入口</option></select></label></div>:<><div className="wm-out-details">{outItem?<dl><div><dt>料箱类型</dt><dd>{boxName(outItem)}</dd></div>{outItem.container_type!=='EMPTY_BIN'&&<><div><dt>物料编码</dt><dd>{outItem.sku}</dd></div><MaterialRows item={outItem}/><div><dt>数量 / 批次</dt><dd>{outItem.quantity} / {outItem.batch||'—'}</dd></div></>}<div><dt>当前库位</dt><dd>{storedLocation(outItem.location)}</dd></div><div><dt>库存状态</dt><dd>{stockNames[outItem.status]}</dd></div></dl>:<Empty title="扫码或搜索在库料箱">选择实际在库料箱后，自动带出物料和库位信息</Empty>}</div><label>出口<select aria-label="出口" value={state.docks.outbound} onChange={()=>{}}><option value={state.docks.outbound}>{state.docks.outbound} 号出口</option></select></label></>}
  {!inbound&&outItem&&(outItem.container_type||'MATERIAL')!==form.container_type&&<p className="wm-error">该条码是{boxName(outItem)}，请切换料箱类型后重新读取。</p>}<p className="wm-help wm-inline"><Info size={18}/>扫码只登记，核对后下发</p>
  {!inbound&&outItem?.status==='IN_STOCK'&&!outLocation?.enabled&&<p className="wm-error" role="status">源库位已停用，不能出库。请先在库位详情中重新启用。</p>}
  {!canEdit&&<p className="wm-help">请先在右上角解锁操作。</p>}
  {inbound&&!emptyLocations.length&&<p className="wm-help">暂无已核对空位。<button type="button" className="wm-link" onClick={()=>onPage('locations')}>前往库位核对 →</button></p>}
  <div className="wm-registration-actions"><button type="submit" value="draft" disabled={!canEdit||busy||reading||(!empty&&(material.pending||material.error))||(!inbound&&!validOut)||(inbound&&!emptyLocations.length)}>加入本次清单</button><button className="wm-primary" type="submit" value="single" disabled={!canEdit||busy||reading||(!empty&&(material.pending||material.error))||(!inbound&&!validOut)||(inbound&&!emptyLocations.length)}><Plus size={24}/>{busy?'处理中…':`创建${empty?'空箱':''}${inbound?'入库':'出库'}任务`}</button></div>
  {!inbound&&outItem?.status==='AT_EXIT'&&<button type="button" className="wm-primary wm-full wm-spaced" disabled={!canEdit||outTask?.status!=='AWAIT_PICKUP'} onClick={()=>setHandover(true)}>出口扫码确认取走</button>}
 </form>
 {draft.tasks.length>0&&<section className="wm-draft" aria-label="本次任务清单"><h3>本次清单 · {draft.tasks.length} 条未登记</h3><p className="wm-help">清单保存在当前标签页；整批核对通过后才创建任务。登记不会自动下发。</p>{draft.tasks.map((row,index)=><div className="wm-draft-row" key={row.request_id}><span>{index+1}. {taskLabel(row)} · <b>{row.barcode}</b><small>{row.location==='AUTO'?(Number(row.depth)?`自动推荐 · ${Number(row.depth)===2?'双伸':'单伸'}`:'自动推荐 · 按库位设置'):storedLocation(row.location)}</small></span><button type="button" disabled={busy} aria-label={`移除 ${row.barcode}`} onClick={()=>setDraft(previous=>({request_id:requestId(),tasks:previous.tasks.filter(item=>item.request_id!==row.request_id)}))}>移除</button></div>)}<button type="button" className="wm-primary wm-full" disabled={!canEdit||busy} onClick={submitDraft}>{busy?'提交中…':`登记清单中的 ${draft.tasks.length} 条任务`}</button></section>}
 </section><aside className="wm-panel wm-worklist"><h2>待办任务</h2>{pending.length?<div className="wm-pending-list">{pending.map(task=><article className="wm-pending-card" key={task.id} aria-label={`${task.number} ${task.barcode}`}><div className="wm-pending-heading"><strong>{taskLabel(task)} · {task.barcode}</strong><Badge status={task.status}/></div><p>{task.number} · {storedLocation(task.location)}</p><p>{task.container_type==='EMPTY_BIN'?'空箱 · 1 箱':`${task.material_name||task.sku} · 数量 ${task.quantity}`}</p><p className="wm-help">{task.detail}</p><TaskButtons task={task} state={state} canEdit={canEdit} onAction={kind=>setAction({task,kind})}/></article>)}</div>:<Empty title="未有待办任务">扫码料箱后可登记作业</Empty>}<PlcSummary state={state} onOpen={()=>onPage('device')}/></aside></div><TaskTable tasks={state.tasks.slice(0,6)} onOpen={()=>onPage('tasks')}/>
 {action&&<TaskActionDialog action={action} state={state} canEdit={canEdit} refresh={refresh} notify={notify} onClose={()=>setAction(null)}/>}
 {handover&&<ConfirmDialog title="出口扫码交接" label="已核对料箱条码，并已从出口取走" button="确认出库交接" onClose={()=>setHandover(false)} onConfirm={async()=>{await api('/api/handover',{request_id:formId,barcode:form.barcode.trim(),confirmed:true});notify('出库交接已完成');setLookup(null);setForm(previous=>({...previous,barcode:''}));setFormId(requestId());await refresh();input.current?.focus();}}><p className="wm-review">料箱 <b>{form.barcode}</b> · 出口 {state.docks.outbound}</p></ConfirmDialog>}
 </>;
}
