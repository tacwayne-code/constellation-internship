import React,{useEffect,useRef,useState} from 'react';
import {api,requestId,storedLocation} from './api';
import {Modal,MaterialFields,PlcSummary,ScanHint,Empty} from './components';
import {materialDetails,boxContents} from './material';
import {transferSources} from './transfer';
import MaterialPicker from './MaterialPicker';
import OrderCards from './OrderCards';
import Pager from './Pager';
import {usePagination,useViewport} from './paging';

const emptyLine=()=>({id:requestId(),sku:'',...materialDetails(),quantity:1,batch:''});
const emptyDocument=()=>({request_id:requestId(),items:[],box_mode:'NEW',box_barcode:''});
const payloadLine=line=>({sku:line.sku.trim(),...materialDetails(line),quantity:Number(line.quantity),batch:line.batch.trim()});
export default function InboundOrderPage({state,canEdit,refresh,notify,onPage}){
 const key=`wms-inbound-document-draft-v1-${state.scope.device_id}-${state.user.id}`;
 const [document,setDocument]=useState(()=>{try{const value=JSON.parse(sessionStorage.getItem(key));if(value?.items)return value;
  const old=JSON.parse(sessionStorage.getItem(`wms-material-order-draft-v2-${state.scope.device_id}-${state.user.id}-INBOUND`));
  if(old?.orders?.length){const boxes=new Set(old.orders.map(row=>row.box_mode==='EXISTING'?row.box_barcode:'')),shared=boxes.size===1?[...boxes][0]:'';return {...emptyDocument(),items:old.orders.map(row=>({id:row.request_id,...payloadLine(row)})),...(shared?{box_mode:'EXISTING',box_barcode:shared}:{})};}
 }catch{}return emptyDocument();});
 const [form,setForm]=useState(emptyLine),[query,setQuery]=useState(''),[known,setKnown]=useState(false),[editing,setEditing]=useState(null),[showItems,setShowItems]=useState(false),[showBoxes,setShowBoxes]=useState(false),[boxQuery,setBoxQuery]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const scanner=useRef(null),formRef=useRef(null),busyRef=useRef(false),{height}=useViewport();
 const boxes=transferSources(state),emptyCount=boxes.filter(box=>box.container_type==='EMPTY_BIN').length;
 const selectedBox=boxes.find(box=>box.barcode===document.box_barcode);
 const boxRows=boxes.filter(box=>[box.barcode,...boxContents(box).map(line=>line.sku),storedLocation(box.location)].join(' ').toLowerCase().includes(boxQuery.trim().toLowerCase()));
 const boxPaging=usePagination(boxRows,Math.max(2,Math.min(4,Math.floor((height-320)/75))),boxQuery);
 const itemPaging=usePagination(document.items,Math.max(2,Math.min(5,Math.floor((height-300)/70))),document.items.length);
 const validLine=Boolean(form.sku.trim()&&Number.isInteger(Number(form.quantity))&&Number(form.quantity)>0&&Number(form.quantity)<=1000000);
 const canCreate=canEdit&&!busy&&!editing&&!query.trim()&&document.items.length>0&&(document.box_mode==='NEW'?emptyCount>0:Boolean(selectedBox));
 useEffect(()=>{sessionStorage.setItem(key,JSON.stringify(document));},[key,document]);
 function changeDocument(patch){setDocument(old=>({...old,...patch,request_id:requestId()}));setError('');}
 function resetLine(){setForm(emptyLine());setQuery('');setKnown(false);setEditing(null);scanner.current?.focus();}
 function addMaterial(event){event.preventDefault();if(!canEdit||busyRef.current||!validLine||!formRef.current.reportValidity())return;
  if(!editing&&document.items.length>=50){setError('一张入库单最多添加 50 条物料');return;}
  const row={id:form.id,...payloadLine(form)};
  changeDocument({items:editing?document.items.map(line=>line.id===editing?row:line):[...document.items,row]});resetLine();notify(editing?'物料已修改':'已添加物料，全部添加完成后创建一张入库单');
 }
 function editLine(line){setForm({...line});setQuery(line.sku);setKnown(false);setEditing(line.id);setShowItems(false);setError('');scanner.current?.focus();}
 function removeLine(line){changeDocument({items:document.items.filter(row=>row.id!==line.id)});}
 async function createDocument(){if(!canCreate||busyRef.current)return;busyRef.current=true;setBusy(true);setError('');
  try{const order=await api('/api/orders/inbound',{request_id:document.request_id,box_mode:document.box_mode,box_barcode:document.box_barcode,items:document.items.map(payloadLine)});
   await refresh();setDocument(emptyDocument());setShowItems(false);resetLine();sessionStorage.removeItem(`wms-material-order-draft-v2-${state.scope.device_id}-${state.user.id}-INBOUND`);notify(`${order.number} 已创建：${order.lines.length} 条物料，共用料箱 ${order.barcode}`);
  }catch(e){setError(e.message);}finally{busyRef.current=false;setBusy(false);}
 }
 return <div className="wm-workspace"><section className="wm-panel wm-registration wm-inbound-document"><h2>{editing?'修改入库物料':'添加入库物料'}</h2><p className="wm-help">先添加本次全部物料，再统一选择一个料箱，创建一张入库单。</p>
 <form ref={formRef} onSubmit={addMaterial}><MaterialPicker value={query} selectedSku={form.sku} inputRef={scanner} disabled={busy} onChange={value=>{setQuery(value);setForm(old=>({...old,sku:'',...materialDetails()}));setKnown(false);}} onSelect={(code,item)=>{setQuery(code);setForm(old=>({...old,sku:code,...materialDetails(item)}));setKnown(Boolean(item));}}/><ScanHint value={query}/>
 <div className="wm-form-grid"><MaterialFields value={form} onChange={(name,value)=>setForm(old=>({...old,[name]:value}))} disabled={busy||known}/><label>本次添加数量<input type="number" required min="1" max="1000000" step="1" value={form.quantity} disabled={busy} onChange={e=>setForm(old=>({...old,quantity:e.target.value}))}/></label><label>批次<input value={form.batch} maxLength={80} disabled={busy} placeholder="选填，追加时沿用原批次" onChange={e=>setForm(old=>({...old,batch:e.target.value}))}/></label></div>
 <div className="wm-registration-actions"><button type="submit" className="wm-primary" disabled={!validLine||!canEdit||busy}>{editing?'保存物料':'添加物料'}</button>{query.trim()&&<button type="button" disabled={busy} onClick={resetLine}>{editing?'取消修改':'清空输入'}</button>}</div></form>
 <div className="wm-review wm-inbound-summary"><p>本单料箱：{document.box_mode==='NEW'?`系统分配一个空箱（可用 ${emptyCount}）`:document.box_barcode+(selectedBox?'':' · 不可用')}</p><button type="button" disabled={busy} onClick={()=>setShowBoxes(true)}>选择料箱</button><p className="wm-help">全部 {document.items.length} 条物料放入同一箱，返库后分别记账。</p></div>
 {error&&<p className="wm-error" role="alert">{error}</p>}{query.trim()&&document.items.length>0&&<p className="wm-help">请先添加当前物料，或清空输入后创建入库单。</p>}{!canEdit&&<p className="wm-help">需具备入库权限并启用设备连接。</p>}
 <div className="wm-draft-summary"><span><b>已添加物料</b> · {document.items.length} 条</span><button type="button" disabled={!document.items.length||busy} onClick={()=>setShowItems(true)}>查看物料</button><button type="button" className="wm-primary" disabled={!canCreate} onClick={createDocument}>{busy?'创建中…':'创建入库单'}</button></div>
 </section><aside className="wm-worklist"><section className="wm-panel wm-pending-panel"><div className="wm-panel-title"><h2>待办入库单</h2><span className="wm-help">{(state.orders||[]).filter(order=>order.kind==='INBOUND'&&!['COMPLETED','CANCELLED'].includes(order.status)).length} 张</span></div><OrderCards {...{state:{...state,orders:state.orders?.filter(order=>order.kind==='INBOUND')},canEdit,refresh,notify}}/></section><PlcSummary state={state} onOpen={()=>onPage('device')}/></aside>
 {showItems&&<Modal title="本张入库单物料" onClose={()=>setShowItems(false)}><p className="wm-help">以下物料合并创建一张入库单，共用同一料箱。</p><div className="wm-draft-list">{itemPaging.items.map(line=><div className="wm-draft-row" key={line.id}><span><b>{line.sku} · {line.material_name||'—'}</b> × {line.quantity}<small>{line.model||'—'} · {line.specification||'—'} · 批次 {line.batch||'—'}</small></span><div className="wm-tool-buttons"><button type="button" disabled={busy} aria-label={`修改物料 ${line.sku}`} onClick={()=>editLine(line)}>修改</button><button type="button" disabled={busy} aria-label={`移除物料 ${line.sku}`} onClick={()=>removeLine(line)}>移除</button></div></div>)}</div>{!document.items.length&&<Empty title="尚未添加物料"/>}<Pager paging={itemPaging} label="入库物料"/></Modal>}
 {showBoxes&&<Modal title="本张入库单料箱" onClose={()=>setShowBoxes(false)}><p className="wm-review">本单全部物料统一放入一个箱，不会按物料分别创建单据。</p><button type="button" className="wm-primary wm-full" disabled={busy||!emptyCount} onClick={()=>{changeDocument({box_mode:'NEW',box_barcode:''});setShowBoxes(false);}}>系统分配一个空箱</button><label>搜索料箱<input value={boxQuery} placeholder="箱号、物料编码或库位" onChange={e=>setBoxQuery(e.target.value)}/></label><div className="wm-choice-list">{boxPaging.items.map(box=><button type="button" className="wm-stock-choice" key={box.barcode} disabled={busy} aria-label={`本单使用料箱 ${box.barcode}`} onClick={()=>{changeDocument({box_mode:'EXISTING',box_barcode:box.barcode});setShowBoxes(false);}}><strong>{box.barcode}</strong><span>{boxContents(box).length} 种物料 · 总数量 {box.quantity} · {storedLocation(box.location)}</span></button>)}</div><Pager paging={boxPaging} label="入库料箱"/>{!boxes.length&&<p className="wm-help">暂无可用料箱，请先处理现有任务或登记真实空箱。</p>}</Modal>}
 </div>;
}
