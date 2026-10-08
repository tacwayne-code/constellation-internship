import React,{useEffect,useRef,useState} from 'react';
import {api,requestId,kindNames,storedLocation} from './api';
import {Modal,MaterialFields,PlcSummary,ScanHint} from './components';
import {materialDetails} from './material';
import OutboundChoices from './OutboundChoices';
import OrderCards from './OrderCards';
import DraftTray from './DraftTray';
import Pager from './Pager';
import {usePagination,useViewport} from './paging';

const blank=()=>({request_id:requestId(),sku:'',...materialDetails(),quantity:1,batch:'',box_mode:'NEW',box_barcode:''});
export default function MaterialOrderPage({kind,state,canEdit,refresh,notify,onPage}){
 const inbound=kind==='INBOUND',busyRef=useRef(false),lookupVersion=useRef(0),scanner=useRef(null),completedCode=useRef('');
 const [form,setForm]=useState(blank),[query,setQuery]=useState(''),[selected,setSelected]=useState(null),[lookup,setLookup]=useState(null),[lookupError,setLookupError]=useState(''),[pending,setPending]=useState(false),[choose,setChoose]=useState(false),[chosen,setChosen]=useState(false),[busy,setBusy]=useState(false),[lookupTick,setLookupTick]=useState(0);
 const draftKey=`wms-material-order-draft-v1-${kind}`;
 const [draft,setDraft]=useState(()=>{try{const saved=JSON.parse(sessionStorage.getItem(draftKey));return saved?.orders?saved:{request_id:requestId(),orders:[]};}catch{return {request_id:requestId(),orders:[]};}});
 const {height}=useViewport();
 const boxes=(lookup?.boxes||[]).filter(box=>!draft.orders.some(row=>row.box_barcode===box.barcode));
 const boxPaging=usePagination(boxes,Math.max(2,Math.min(5,Math.floor((height-350)/72))),form.sku);
 useEffect(()=>{sessionStorage.setItem(draftKey,JSON.stringify(draft));},[draftKey,draft]);
 useEffect(()=>{
  if(!inbound||!form.sku.trim()){setLookup(null);setPending(false);return;}
  const version=++lookupVersion.current,controller=new AbortController();setPending(true);setLookupError('');setLookup(null);setChosen(false);setChoose(false);
  const timer=setTimeout(async()=>{try{
   const result=await api(`/api/orders/options?sku=${encodeURIComponent(form.sku.trim())}`,undefined,{signal:controller.signal});
   if(version!==lookupVersion.current)return;
   setLookup(result);setForm(previous=>({...previous,...materialDetails(result.material||{}),box_mode:'NEW',box_barcode:'',request_id:requestId()}));
   // A keyboard scanner may pause between packets. Background lookup may fill
   // metadata, but must never steal focus before Enter/Tab or an explicit blur.
   setChosen(!result.material);setChoose(Boolean(result.material)&&completedCode.current===form.sku.trim());
  }catch(error){if(!controller.signal.aborted)setLookupError(error.message);}finally{if(!controller.signal.aborted&&version===lookupVersion.current)setPending(false);}},250);
  return()=>{clearTimeout(timer);controller.abort();lookupVersion.current++;};
 },[inbound,form.sku,lookupTick]);
 function update(key,value){setForm(previous=>({...previous,[key]:value,request_id:requestId()}));if(key==='sku'){completedCode.current='';setPending(Boolean(value.trim()));setLookup(null);setChosen(false);setChoose(false);}}
 function completeCode(value){
  const code=value.trim();if(!code||completedCode.current===code)return;completedCode.current=code;
  if(lookup&&!pending)setChoose(Boolean(lookup.material));
  else if(lookupError)setLookupTick(tick=>tick+1);
 }
 function reset(){completedCode.current='';setForm(blank());setQuery('');setSelected(null);setLookup(null);setChoose(false);setChosen(false);setLookupError('');scanner.current?.focus();}
 function select(item){setSelected(item);setQuery(item.barcode);setForm({...blank(),sku:item.sku,...materialDetails(item),batch:item.batch||'',box_mode:'EXISTING',box_barcode:item.barcode});}
 function eligible(item){const loc=state.locations.find(row=>row.id===item.location);return item.status==='IN_STOCK'&&!item.reserved&&loc?.enabled&&loc?.verified&&!loc.reserved&&!loc.access_error&&(item.container_type||'MATERIAL')==='MATERIAL'&&!draft.orders.some(row=>row.box_barcode===item.barcode);}
 function scanOutbound(value){const item=state.stock.find(row=>row.barcode===value.trim()&&eligible(row));if(item)select(item);}
 useEffect(()=>{if(inbound||!query.trim()||selected)return;const timer=setTimeout(()=>scanOutbound(query),250);return()=>clearTimeout(timer);},[query,inbound,selected,state.stock]);
 function selectMode(box){setForm(previous=>({...previous,box_mode:box?'EXISTING':'NEW',box_barcode:box?.barcode||'',batch:box?.batch||'',request_id:requestId()}));setChosen(true);setChoose(false);}
 const valid=inbound?Boolean(form.sku.trim()&&lookup&&chosen&&!pending&&!lookupError):Boolean(selected&&eligible(state.stock.find(row=>row.barcode===selected.barcode)||selected));
 async function submit(event){
  event.preventDefault();if(!valid||!canEdit||busyRef.current)return;
  const row={...form,kind,sku:form.sku.trim(),quantity:Number(form.quantity)};
  if(event.nativeEvent.submitter?.value==='draft'){
   if(draft.orders.length>=50){notify('每批最多 50 条单据，请先提交','error');return;}
   if(row.box_barcode&&draft.orders.some(item=>item.box_barcode===row.box_barcode)){notify('本次清单已有该料箱，请合并数量或选择另一箱','error');return;}
   setDraft(previous=>({request_id:requestId(),orders:[...previous.orders,row]}));reset();return;
  }
  busyRef.current=true;setBusy(true);
  try{const order=await api('/api/orders',row);await refresh();reset();notify(`${order.number} 已登记，分配料箱 ${order.barcode}，请核对取箱`);}catch(error){notify(error.message,'error');}finally{busyRef.current=false;setBusy(false);}
 }
 async function submitDraft(){
  if(!canEdit||busyRef.current)return;busyRef.current=true;setBusy(true);
  try{const result=await api('/api/orders/batch',draft);await refresh();setDraft({request_id:requestId(),orders:[]});notify(`已登记 ${result.count} 条单据，按顺序核对取箱`);}catch(error){notify(error.message,'error');}finally{busyRef.current=false;setBusy(false);}
 }
 return <div className="wm-workspace"><section className="wm-panel wm-registration"><h2>创建物料{kindNames[kind]}单</h2><p className="wm-help">{inbound?'系统先取出分配的料箱，人工装料后返库。无需输入新箱箱号。':'选择在库物料箱，取箱到出口后扫码核对，按本单数量取料，再返库。'}</p><form onSubmit={submit}>
 {inbound?<label>物料编码<input ref={scanner} autoFocus autoComplete="off" spellCheck={false} value={form.sku} disabled={busy} maxLength={80} required placeholder="扫描或输入物料编码" onChange={e=>update('sku',e.target.value)} onBlur={e=>completeCode(e.currentTarget.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.nativeEvent.isComposing){e.preventDefault();completeCode(e.currentTarget.value);}}}/></label>:<><label>扫码或搜索物料<input ref={scanner} autoFocus autoComplete="off" value={query} disabled={busy} maxLength={200} placeholder="条码、编码、名称、型号、规格" onChange={e=>{setQuery(e.target.value);setSelected(null);}} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();scanOutbound(e.currentTarget.value);}}}/></label>{!selected&&<OutboundChoices query={query} state={state} busy={busy} onSelect={select} excluded={draft.orders.map(row=>row.box_barcode)}/>}</>}
 <ScanHint value={inbound?form.sku:query}/>
 {pending&&<p className="wm-help" role="status">正在查询物料档案…</p>}{lookupError&&<p className="wm-error" role="alert">{lookupError}<button type="button" onClick={()=>setLookupTick(value=>value+1)}>重试查询</button></p>}
 {(inbound||selected)&&<>{inbound?<div className="wm-form-grid"><MaterialFields value={form} onChange={update} disabled={busy||pending||Boolean(lookup?.material)}/><label>本单入库数量<input type="number" min="1" max="1000000" step="1" inputMode="numeric" required value={form.quantity} disabled={busy} onChange={e=>update('quantity',e.target.value)}/></label><label>批次<input value={form.batch} disabled={busy||form.box_mode==='EXISTING'} maxLength={80} placeholder="选填" onChange={e=>update('batch',e.target.value)}/></label></div>:<><dl className="wm-info-list wm-out-material-grid">{[['物料编码',form.sku],['物料名称',form.material_name||'—'],['型号',form.model||'—'],['规格',form.specification||'—'],['批次',form.batch||'—']].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><label>本单出库数量<input type="number" min="1" max={selected.quantity} step="1" inputMode="numeric" required value={form.quantity} disabled={busy} onChange={e=>update('quantity',e.target.value)}/></label></>}

 {inbound?<div className="wm-review"><p>{!lookup?'扫描编码后确定配箱方式':!chosen?'该编码已有档案，请选择新箱或已有物料箱':form.box_mode==='NEW'?`系统分配空箱 · 当前可用 ${lookup.empty_count} 箱`:`已有料箱 ${form.box_barcode}`}</p>{lookup?.material&&<button type="button" disabled={busy||pending} onClick={()=>setChoose(true)}>选择新箱或已有物料箱</button>}<p className="wm-help">提交单据时预留料箱和原库位；返库完成后增加本单数量。</p></div>:<div className="wm-review"><p>料箱 <b>{selected.barcode}</b> · {storedLocation(selected.location)}</p><p>箱内现有 {selected.quantity}，本次取出 {Number(form.quantity)||0}，返库后剩余 {selected.quantity-(Number(form.quantity)||0)}</p><p className="wm-help">剩余为 0 时自动转为空箱并保留箱号。</p></div>}
 </>}
 {!canEdit&&<p className="wm-help wm-lock-hint">请先在右上角解锁操作。</p>}<div className="wm-registration-actions"><button type="submit" value="draft" disabled={!valid||!canEdit||busy}>加入单据清单</button><button type="submit" value="single" className="wm-primary" disabled={!valid||!canEdit||busy}>{busy?'处理中…':`创建${kindNames[kind]}单`}</button></div>
 </form>
 <DraftTray rows={draft.orders} title="单据清单" submitLabel={`提交 ${draft.orders.length} 条单据`} onSubmit={submitDraft} onRemove={row=>setDraft(previous=>({request_id:requestId(),orders:previous.orders.filter(item=>item.request_id!==row.request_id)}))} {...{busy,canEdit}} renderRow={row=><><b>{row.material_name||row.sku}</b> · {row.quantity}<small>{row.sku} · {row.box_mode==='NEW'?'自动分配空箱':row.box_barcode}</small></>}/>
 </section><aside className="wm-worklist"><section className="wm-panel wm-pending-panel"><div className="wm-panel-title"><h2>待办单据</h2><span className="wm-help">{(state.orders||[]).filter(order=>!['COMPLETED','CANCELLED'].includes(order.status)).length} 条</span></div><OrderCards {...{state,canEdit,refresh,notify}}/></section><PlcSummary state={state} onOpen={()=>onPage('device')}/></aside>
 {choose&&lookup?.material&&<Modal title="选择入库料箱" onClose={()=>setChoose(false)}><p className="wm-review">{form.sku} · {lookup.material.material_name||'未填写名称'}<br/>型号 {lookup.material.model||'—'} · 规格 {lookup.material.specification||'—'}</p><button className="wm-primary wm-full" type="button" disabled={!lookup.empty_count} onClick={()=>selectMode(null)}>使用新箱（系统分配空箱）</button><p className="wm-help">可用空箱 {lookup.empty_count} 个。或选择已有物料箱追加数量：</p><div className="wm-choice-list">{boxPaging.items.map(box=><button type="button" className="wm-stock-choice" key={box.barcode} onClick={()=>selectMode(box)}><strong>使用已有箱 {box.barcode}</strong><span>现有数量 {box.quantity} · {storedLocation(box.location)}</span><small>批次 {box.batch||'—'}</small></button>)}</div><Pager paging={boxPaging} label="已有料箱"/>{!boxes.length&&<p className="wm-help">没有可用的同物料在库料箱，可使用新箱。</p>}</Modal>}
 </div>;
}
