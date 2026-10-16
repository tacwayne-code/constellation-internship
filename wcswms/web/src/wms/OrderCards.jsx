import React,{useRef,useState} from 'react';
import {api,requestId,kindNames,storedLocation,hasPermission,operatorLabel} from './api';
import {Modal,Empty,OrderLines} from './components';
import {TaskButtons,TaskActionDialog} from './TaskActions';
import {usePagination} from './paging';
import Pager from './Pager';
import CountReturnDialog from './CountReturnDialog';
import {orderLines} from './material';

const stages={QUEUED:'待取箱',FETCHING:'取箱中',FETCH_ACK:'取箱回执处理中',WAIT_COUNT:'待盘点核对',WAIT_LOAD:'待人工装料',WAIT_PICK:'待核对取料',RETURN_QUEUED:'待返库下发',RETURNING:'返库中',RETURN_ACK:'返库回执处理中',REVIEW:'待现场核对',COMPLETED:'已完成',CANCELLED:'已取消'};
export default function OrderCards({state,canEdit,refresh,notify,all=false,onlyId}){
 const [action,setAction]=useState(null),[returnId,setReturnId]=useState(null);
 const orders=(state.orders||[]).filter(order=>(all||!['COMPLETED','CANCELLED'].includes(order.status))&&(!onlyId||order.id===onlyId)).slice().reverse();
 const paging=usePagination(orders,1,`${all}:${onlyId||''}`);
 const returning=(state.orders||[]).find(order=>order.id===returnId);
 return <div className="wm-orders"><div className="wm-pending-list">{paging.items.map(order=>{
  const task=state.tasks.find(item=>item.id===order.current_task_id);
  const waiting=['WAIT_LOAD','WAIT_PICK','WAIT_COUNT'].includes(order.status);
  const status=task?.status==='REVIEW'?'待现场核对':task?.status==='QUEUED'?(task.order_leg==='RETURN'?'待返库下发':'待取箱'):stages[order.status]||order.status;
  return <article className="wm-pending-card wm-order-card" key={order.id} aria-label={order.number}><div className="wm-pending-heading"><strong>{kindNames[order.kind]}单 · {order.number}</strong><span className={`wm-badge wm-${order.status.toLowerCase()}`}>{status}</span></div><h3>{orderLines(order).length>1?`${orderLines(order).length} 行物料 · 同箱作业`:order.material_name||order.sku||(order.kind==='COUNT'?'空箱盘点':'')}</h3><OrderLines order={order}/><p className="wm-order-operator"><span>建单：{operatorLabel(order.created_by)}</span><span>操作：{operatorLabel(order.operator)}</span></p><p>料箱 <b>{order.barcode}</b> · 原库位 {storedLocation(order.location)}</p><p className="wm-help">{waiting?(order.kind==='COUNT'?'料箱已到交接点，请核对实际物料、数量和批次，再确认返库。':order.kind==='INBOUND'?'料箱已到交接点，请按本单数量装料，再点击返库。':'料箱已到出口，请按本设备的扫码要求核对，按本单数量取料，再点击返库。'):task?.detail}</p>{order.quantity_posted&&<p className="wm-help">本单数量已过账</p>}
   {waiting&&order.station_confirmation&&<p className="wm-error">之前的人工操作已确认，请勿重复装料或取料。核对箱内数量后重新返库。</p>}
   {waiting?<button className="wm-primary wm-full" disabled={!canEdit||!hasPermission(state,'control')||!hasPermission(state,order.kind==='COUNT'?'inventory':order.kind.toLowerCase())} onClick={()=>setReturnId(order.id)}>{order.kind==='COUNT'?'核对盘点并返库':order.kind==='INBOUND'?'装料完成，返库':'核对取料并返库'}</button>:task&&<TaskButtons task={task} state={state} canEdit={canEdit} onAction={kind=>setAction({task,kind})}/>}
  </article>;
 })}</div>{!orders.length&&<Empty title={all?'暂无物料单据':'暂无待办物料单据'}>登记单据后，按取箱、人工操作、返库顺序执行</Empty>}{!onlyId&&<Pager paging={paging} label="待办单据"/>}
 {action&&<TaskActionDialog action={action} state={state} canEdit={canEdit} refresh={refresh} notify={notify} onClose={()=>setAction(null)}/>}
 {returning&&(returning.kind==='COUNT'?<CountReturnDialog key={returning.id} order={returning} state={state} canEdit={canEdit&&hasPermission(state,'inventory')&&hasPermission(state,'control')} refresh={refresh} notify={notify} onClose={()=>setReturnId(null)}/>:<ReturnDialog key={returning.id} order={returning} state={state} canEdit={canEdit} refresh={refresh} notify={notify} onClose={()=>setReturnId(null)}/>)}
 </div>;
}

function ReturnDialog({order,state,canEdit,refresh,notify,onClose}){
 const outbound=order.kind==='OUTBOUND',skuInput=useRef(null),busyRef=useRef(false),skus=[...new Set(orderLines(order).map(line=>line.sku))];
 const [id]=useState(requestId),[barcode,setBarcode]=useState(''),[scans,setScans]=useState(()=>Object.fromEntries(skus.map(code=>[code,'']))),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const waiting=['WAIT_LOAD','WAIT_PICK','WAIT_COUNT'].includes(order.status);
 const policy=state.outbound_scan_policy||{require_barcode:true,require_sku:true,revision:0};
 const scanPaging=usePagination(skus,2,order.id);
 const matched=!outbound||((!policy.require_barcode||barcode.trim()===order.barcode)&&(!policy.require_sku||skus.every(code=>scans[code]?.trim()===code)));
 const ready=canEdit&&hasPermission(state,'control')&&hasPermission(state,order.kind==='COUNT'?'inventory':order.kind.toLowerCase())&&waiting&&state.readiness.ready&&matched&&confirmed&&!busy;
 async function submit(event){
  event.preventDefault();if(!ready||busyRef.current)return;busyRef.current=true;setBusy(true);setError('');
  try{await api(`/api/orders/${order.id}/return`,{request_id:id,confirmed:true,scanned_barcode:barcode.trim(),scanned_sku:scans[skus[0]]?.trim()||'',scanned_skus:skus.map(code=>scans[code]?.trim()||''),expected_scan_revision:outbound?policy.revision:null});await refresh();notify('返库已提交，匹配完成回执后自动更新数量');onClose();}
  catch(e){setError(e.message);await refresh().catch(()=>{});}finally{busyRef.current=false;setBusy(false);}
 }
 return <Modal title={outbound?'核对取料并返库':'确认装料并返库'} onClose={()=>{if(!busy)onClose();}}><form onSubmit={submit}>
  <div className="wm-review"><p><b>{order.number} · 箱号 {order.barcode}</b></p><p>请按以下明细{outbound?'取出':'装入'}物料：</p><OrderLines order={order}/><p>返库后全箱数量 {order.box_after_total??order.after_quantity} · 返回 {storedLocation(order.location)}</p>{(order.box_after_total??order.after_quantity)===0&&<p>全部物料取完后转为空箱，保留箱号。</p>}</div>
  {outbound&&<><p className="wm-help">箱号{policy.require_barcode?'需要扫码':'无需扫码'} · 物料编码{policy.require_sku?'需要扫码':'无需扫码'}</p>{policy.require_barcode&&<label>扫描箱号<input autoFocus autoComplete="off" value={barcode} maxLength={64} onChange={e=>setBarcode(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();if(e.currentTarget.value.trim()===order.barcode)skuInput.current?.focus();}}}/><span className={barcode&&barcode.trim()!==order.barcode?'wm-error':'wm-help'}>{barcode.trim()===order.barcode?'箱号已匹配':'请扫描本单实际料箱条码'}</span></label>}{policy.require_sku&&scanPaging.items.map((code,index)=><label key={code}>扫描物料编码 {code}<input ref={index===0?skuInput:undefined} autoFocus={!policy.require_barcode&&index===0} autoComplete="off" value={scans[code]||''} maxLength={80} onChange={e=>setScans(old=>({...old,[code]:e.target.value}))} onKeyDown={e=>{if(e.key==='Enter')e.preventDefault();}}/><span className={scans[code]&&scans[code].trim()!==code?'wm-error':'wm-help'}>{scans[code]?.trim()===code?'物料已匹配':'请扫描此行物料编码核对'}</span></label>)}{policy.require_sku&&<Pager paging={scanPaging} label="物料扫码"/>}</>}
  <label className="wm-check"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>已按本单数量{outbound?'取料':'装料'}，料箱已就位，允许返库</label>
  {!state.readiness.ready&&<p className="wm-error">设备待就绪：{state.readiness.reasons.join('；')}</p>}
  {!waiting&&<p className="wm-help">返库步骤已创建。请关闭弹窗，在单据卡片查看进度或重试下发。</p>}
  {error&&<p className="wm-error" role="alert">{error}</p>}<button className="wm-primary wm-full" disabled={!ready}>{busy?'提交中…':'确认并下发返库'}</button>
 </form></Modal>;
}
