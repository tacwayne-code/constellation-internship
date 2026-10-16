import React,{useRef,useState} from 'react';
import {Search} from 'lucide-react';
import {api,requestId,storedLocation,boxName} from './api';
import {Empty,Modal,BoxContents} from './components';
import {boxContents} from './material';
import {transferSources,transferMatches} from './transfer';
import {usePagination,useViewport} from './paging';
import Pager from './Pager';
import OrderCards from './OrderCards';

export default function CountPage({state,canEdit,refresh,notify}){
 const [query,setQuery]=useState(''),[selected,setSelected]=useState(null),[showContents,setShowContents]=useState(false),[note,setNote]=useState('现场库存盘点'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[pane,setPane]=useState('create'),[range,setRange]=useState('active');
 const [id,setId]=useState(requestId),busyRef=useRef(false);
 const {height,width}=useViewport();
 const boxes=transferSources(state).filter(box=>transferMatches([box.barcode,...boxContents(box).flatMap(line=>[line.sku,line.material_name,line.model,line.specification]),storedLocation(box.location)].join(' '),query));
 const paging=usePagination(boxes,height<650?2:3,query),box=transferSources(state).find(item=>item.barcode===selected);
 const orders=(state.orders||[]).filter(order=>order.kind==='COUNT');
 async function create(){if(!box||busyRef.current)return;busyRef.current=true;setBusy(true);setError('');try{const order=await api('/api/orders/count',{request_id:id,box_barcode:box.barcode,expected_updated_at:box.updated_at,note});await refresh();setSelected(null);setId(requestId());if(width<760||height<=560)setPane('pending');notify(`${order.number} 已创建，请核对取箱出库`);}catch(e){setError(e.message);}finally{busyRef.current=false;setBusy(false);}}
 return <div className="wm-count-page" data-pane={pane}><div className="wm-section-tools"><p className="wm-help">整箱取出盘点，确认实盘信息后返库；返库完成后统一过账。</p><button className="wm-mobile-toggle" onClick={()=>setPane(pane==='create'?'pending':'create')}>{pane==='create'?'查看盘点单':'返回创建'}</button></div><div className="wm-workspace">
  <section className="wm-panel wm-count-create"><div className="wm-panel-title"><h2>创建盘点单</h2><span className="wm-help">空箱和物料箱均可盘点</span></div><div className="wm-search"><Search size={17}/><input aria-label="搜索可盘点料箱" placeholder="料箱、物料、型号、规格或库位" value={query} onChange={e=>setQuery(e.target.value)}/></div>
  <div className="wm-count-options">{paging.items.map(item=>{const lines=boxContents(item),first=lines.find(line=>transferMatches([line.sku,line.material_name,line.model,line.specification].join(' '),query))||lines[0];return <button className={`wm-stock-choice ${box?.barcode===item.barcode?'wm-count-selected':''}`} key={item.barcode} aria-label={`盘点料箱 ${item.barcode}`} aria-pressed={box?.barcode===item.barcode} onClick={()=>{setSelected(item.barcode);setId(requestId());}} disabled={busy}><strong>{item.barcode}<span>{boxName(item)} · 数量 {item.quantity}</span></strong><span>{storedLocation(item.location)} · {first?.material_name||first?.sku||'无物料'}{lines.length>1?` 等 ${lines.length} 种`:''}</span>{first&&<small>型号 {first.model||'—'} · 规格 {first.specification||'—'}</small>}</button>;})}</div>{!boxes.length&&<Empty title="暂无可盘点料箱">只列出未预留、库位启用且通道可用的在库料箱。</Empty>}<Pager paging={paging} label="可盘点料箱"/>
  <label>盘点说明<input value={note} minLength={2} maxLength={200} disabled={busy} onChange={e=>{setNote(e.target.value);setId(requestId());}}/></label>{box&&<div className="wm-review wm-count-selection"><span>已选 {box.barcode} · {storedLocation(box.location)} · {boxName(box)}</span><button type="button" className="wm-link" onClick={()=>setShowContents(true)}>箱内明细</button></div>}{error&&<p className="wm-error" role="alert">{error}</p>}<button className="wm-primary wm-full" disabled={!canEdit||!box||note.trim().length<2||busy} onClick={create}>{busy?'创建中…':'创建盘点单'}</button></section>
  <section className="wm-panel wm-pending-panel" onClickCapture={()=>setPane('pending')}><div className="wm-panel-title"><h2>盘点单</h2><div className="wm-tabs" aria-label="盘点范围"><button className={range==='active'?'selected':''} onClick={()=>setRange('active')}>进行中 · {orders.filter(order=>!['COMPLETED','CANCELLED'].includes(order.status)).length}</button><button className={range==='all'?'selected':''} onClick={()=>setRange('all')}>全部盘点</button></div></div><OrderCards state={{...state,orders}} {...{canEdit,refresh,notify}} all={range==='all'}/></section>
 </div>{showContents&&box&&<Modal title={`盘点料箱明细 · ${box.barcode}`} onClose={()=>setShowContents(false)}><BoxContents item={box} pageSize={height<650?1:3}/></Modal>}</div>;
}
