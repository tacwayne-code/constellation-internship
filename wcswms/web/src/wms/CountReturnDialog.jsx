import React,{useRef,useState} from 'react';
import {api,requestId,storedLocation} from './api';
import {Modal,MaterialFields} from './components';
import {materialDetails} from './material';
import MaterialPicker from './MaterialPicker';
import {usePagination,useViewport} from './paging';
import Pager from './Pager';

export default function CountReturnDialog({order,state,canEdit,refresh,notify,onClose}){
 const [rows,setRows]=useState(()=>structuredClone(order.count_confirmed_at?order.after_contents:order.before_contents));
 const [note,setNote]=useState(order.count_note||''),[confirmed,setConfirmed]=useState(false),[adding,setAdding]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [id,setId]=useState(requestId),busyRef=useRef(false),{height}=useViewport();
 const paging=usePagination(rows,height<600?1:height<750?2:4,order.id);
 function changed(next){setRows(next);setConfirmed(false);setId(requestId());setError('');}
 const valid=rows.every(row=>row.sku&&Number.isInteger(Number(row.quantity))&&Number(row.quantity)>=0&&Number(row.quantity)<=1000000)&&rows.reduce((sum,row)=>sum+Number(row.quantity),0)<=1000000;
 const ready=canEdit&&state.readiness.ready&&order.status==='WAIT_COUNT'&&confirmed&&valid&&note.trim().length>=2&&!busy;
 async function submit(event){
  event.preventDefault();if(!ready||busyRef.current)return;busyRef.current=true;setBusy(true);setError('');
  try{await api(`/api/orders/${order.id}/count-return`,{request_id:id,confirmed:true,note,items:rows.map(row=>({...row,quantity:Number(row.quantity)}))});await refresh();notify('盘点已确认并提交返库，匹配完成回执后更新库存');onClose();}
  catch(e){setError(e.message);}finally{busyRef.current=false;setBusy(false);}
 }
 return <Modal title={adding?'添加盘点物料':'核对盘点并返库'} className="wm-count-dialog" onClose={()=>{if(!busy)onClose();}}>{adding?<AddCountMaterial onCancel={()=>setAdding(false)} onAdd={row=>{if(rows.some(item=>item.sku===row.sku))throw new Error('明细已有该物料，请修改原行数量');changed([...rows,row]);setAdding(false);}}/>:<form onSubmit={submit}>
  <p className="wm-review"><b>{order.number} · {order.barcode}</b><br/>返回 {storedLocation(order.location)}。请核对实际物料、数量及批次；未列出的原物料按 0 记账，实盘全为 0 时返库为空箱。</p>
  <div className="wm-panel-title"><h3>实盘明细 · {rows.length} 种</h3><button type="button" disabled={busy||rows.length>=50} onClick={()=>setAdding(true)}>添加物料</button></div>
  <table className="wm-count-lines"><thead><tr><th>物料 / 账面数量</th><th>实盘数量</th><th>批次</th><th>操作</th></tr></thead><tbody>{paging.items.map(row=><tr key={row.sku}><td><strong>{row.sku}</strong><small>{row.material_name||'—'} · 账面 {order.before_contents.find(item=>item.sku===row.sku)?.quantity||0}</small><small>型号 {row.model||'—'} · 规格 {row.specification||'—'}</small></td><td><input aria-label={`实盘数量 ${row.sku}`} type="number" min="0" max="1000000" step="1" required value={row.quantity} disabled={busy} onChange={e=>changed(rows.map(item=>item.sku===row.sku?{...item,quantity:e.target.value}:item))}/></td><td><input aria-label={`盘点批次 ${row.sku}`} value={row.batch} maxLength={80} disabled={busy} onChange={e=>changed(rows.map(item=>item.sku===row.sku?{...item,batch:e.target.value}:item))}/></td><td><button type="button" className="wm-table-action" aria-label={`移除盘点物料 ${row.sku}`} disabled={busy} onClick={()=>changed(rows.filter(item=>item.sku!==row.sku))}>移除</button></td></tr>)}</tbody></table>
  {!rows.length&&<p className="wm-help">当前按空箱核对，可添加现场发现的物料。</p>}<Pager paging={paging} label="盘点物料"/>
  <label>盘点说明<input value={note} minLength={2} maxLength={200} required disabled={busy} onChange={e=>{setNote(e.target.value);setId(requestId());setConfirmed(false);}} placeholder="记录核对结果及差异原因"/></label>
  <label className="wm-check"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e=>setConfirmed(e.target.checked)}/>已核对实际物料、数量、批次及料箱就位，确认返库</label>
  {!state.readiness.ready&&<p className="wm-error">设备待就绪：{state.readiness.reasons.join('；')}</p>}{!valid&&<p className="wm-error">数量须为非负整数，全箱总数量不能超过 1000000。</p>}{error&&<p className="wm-error" role="alert">{error}</p>}
  <button className="wm-primary wm-full" disabled={!ready}>{busy?'提交中…':'确认盘点并下发返库'}</button>
 </form>}</Modal>;
}

function AddCountMaterial({onAdd,onCancel}){
 const [query,setQuery]=useState(''),[known,setKnown]=useState(false),[form,setForm]=useState({sku:'',quantity:1,batch:'',...materialDetails()}),[error,setError]=useState('');
 return <form onSubmit={event=>{event.preventDefault();try{onAdd({...form,quantity:Number(form.quantity)});}catch(e){setError(e.message);}}}><MaterialPicker value={query} selectedSku={form.sku} onChange={value=>{setQuery(value);setKnown(false);setForm(old=>({...old,sku:'',...materialDetails()}));}} onSelect={(sku,item)=>{setQuery(sku);setKnown(Boolean(item));setForm(old=>({...old,sku,...materialDetails(item)}));}}/><div className="wm-form-grid"><MaterialFields value={form} disabled={known} onChange={(key,value)=>setForm(old=>({...old,[key]:value}))}/><label>实盘数量<input type="number" required min="1" max="1000000" step="1" value={form.quantity} onChange={e=>setForm(old=>({...old,quantity:e.target.value}))}/></label><label>批次<input value={form.batch} maxLength={80} onChange={e=>setForm(old=>({...old,batch:e.target.value}))}/></label></div>{error&&<p className="wm-error" role="alert">{error}</p>}<div className="wm-tool-buttons"><button type="button" onClick={onCancel}>返回明细</button><button className="wm-primary" disabled={!form.sku}>添加到实盘明细</button></div></form>;
}
