import React,{useRef,useState} from 'react';
import {api,requestId,kindNames,storedLocation} from './api';
import {Modal,Empty} from './components';
import {TaskButtons,TaskActionDialog} from './TaskActions';

const stages={QUEUED:'待取箱',FETCHING:'取箱中',FETCH_ACK:'取箱回执处理中',WAIT_LOAD:'待人工装料',WAIT_PICK:'待扫码取料',RETURN_QUEUED:'待返库下发',RETURNING:'返库中',RETURN_ACK:'返库回执处理中',REVIEW:'待现场核对',COMPLETED:'已完成',CANCELLED:'已取消'};
export default function OrderCards({state,canEdit,refresh,notify,all=false}){
 const [action,setAction]=useState(null),[returnId,setReturnId]=useState(null);
 const orders=(state.orders||[]).filter(order=>all||!['COMPLETED','CANCELLED'].includes(order.status)).slice().reverse();
 const returning=(state.orders||[]).find(order=>order.id===returnId);
 return <><div className="wm-pending-list">{orders.map(order=>{
  const task=state.tasks.find(item=>item.id===order.current_task_id);
  const waiting=['WAIT_LOAD','WAIT_PICK'].includes(order.status);
  const status=task?.status==='REVIEW'?'待现场核对':task?.status==='QUEUED'?(task.order_leg==='RETURN'?'待返库下发':'待取箱'):stages[order.status]||order.status;
  return <article className="wm-pending-card wm-order-card" key={order.id} aria-label={order.number}><div className="wm-pending-heading"><strong>{kindNames[order.kind]}单 · {order.number}</strong><span className={`wm-badge wm-${order.status.toLowerCase()}`}>{status}</span></div><h3>{order.material_name||order.sku}</h3><p>编码 {order.sku} · 型号 {order.model||'—'} · 规格 {order.specification||'—'}</p><p>本单{kindNames[order.kind]} <b>{order.quantity}</b> · 箱内数量 {order.before_quantity} → <b>{order.after_quantity}</b>{order.after_quantity===0?'（返库后转为空箱）':''}</p><p>料箱 <b>{order.barcode}</b> · 原库位 {storedLocation(order.location)}</p><p className="wm-help">{waiting?(order.kind==='INBOUND'?'料箱已到交接点，请按本单数量装料，再点击返库。':'料箱已到出口，请扫描箱号和物料编码，按本单数量取料，再点击返库。'):task?.detail}</p>{order.quantity_posted&&<p className="wm-help">本单数量已过账</p>}
   {waiting&&order.station_confirmation&&<p className="wm-error">之前的人工操作已确认，请勿重复装料或取料。核对箱内数量后重新返库。</p>}
   {waiting?<button className="wm-primary wm-full" disabled={!canEdit} onClick={()=>setReturnId(order.id)}>{order.kind==='INBOUND'?'装料完成，返库':'扫码核对并返库'}</button>:task&&<TaskButtons task={task} state={state} canEdit={canEdit} onAction={kind=>setAction({task,kind})}/>}
  </article>;
 })}</div>{!orders.length&&<Empty title={all?'暂无物料单据':'暂无待办物料单据'}>登记单据后，按取箱、人工操作、返库顺序执行</Empty>}
 {action&&<TaskActionDialog action={action} state={state} canEdit={canEdit} refresh={refresh} notify={notify} onClose={()=>setAction(null)}/>}
 {returning&&<ReturnDialog key={returning.id} order={returning} state={state} canEdit={canEdit} refresh={refresh} notify={notify} onClose={()=>setReturnId(null)}/>}
 </>;
}

function ReturnDialog({order,state,canEdit,refresh,notify,onClose}){
 const outbound=order.kind==='OUTBOUND',skuInput=useRef(null),busyRef=useRef(false);
 const [id]=useState(requestId),[barcode,setBarcode]=useState(''),[sku,setSku]=useState(''),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const waiting=['WAIT_LOAD','WAIT_PICK'].includes(order.status);
 const matched=!outbound||(barcode.trim()===order.barcode&&sku.trim()===order.sku);
 const ready=canEdit&&waiting&&state.readiness.ready&&matched&&confirmed&&!busy;
 async function submit(event){
  event.preventDefault();if(!ready||busyRef.current)return;busyRef.current=true;setBusy(true);setError('');
  try{await api(`/api/orders/${order.id}/return`,{request_id:id,confirmed:true,scanned_barcode:barcode.trim(),scanned_sku:sku.trim()});await refresh();notify('返库已提交，匹配完成回执后自动更新数量');onClose();}
  catch(e){setError(e.message);await refresh().catch(()=>{});}finally{busyRef.current=false;setBusy(false);}
 }
 return <Modal title={outbound?'扫码核对取料并返库':'确认装料并返库'} onClose={()=>{if(!busy)onClose();}}><form onSubmit={submit}>
  <div className="wm-review"><p><b>{order.number} · {order.material_name||order.sku}</b></p><p>箱号：{order.barcode} · 物料：{order.sku}</p><p>型号：{order.model||'—'} · 规格：{order.specification||'—'}</p><p>请{outbound?'取出':'装入'} <b>{order.quantity}</b>，返库后箱内数量 <b>{order.after_quantity}</b></p><p>返回 {storedLocation(order.location)}</p>{order.after_quantity===0&&<p>数量归零后自动登记为空箱，保留箱号。</p>}</div>
  {outbound&&<><label>扫描箱号<input autoFocus autoComplete="off" value={barcode} maxLength={64} onChange={e=>setBarcode(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();if(e.currentTarget.value.trim()===order.barcode)skuInput.current?.focus();}}}/><span className={barcode&&barcode.trim()!==order.barcode?'wm-error':'wm-help'}>{barcode.trim()===order.barcode?'箱号已匹配':'请扫描本单实际料箱条码'}</span></label><label>扫描物料编码<input ref={skuInput} autoComplete="off" value={sku} maxLength={80} onChange={e=>setSku(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')e.preventDefault();}}/><span className={sku&&sku.trim()!==order.sku?'wm-error':'wm-help'}>{sku.trim()===order.sku?'物料已匹配':'请扫描物料编码进行核对'}</span></label></>}
  <label className="wm-check"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>已按本单数量{outbound?'取料':'装料'}，料箱已就位，允许返库</label>
  {!state.readiness.ready&&<p className="wm-error">设备待就绪：{state.readiness.reasons.join('；')}</p>}
  {!waiting&&<p className="wm-help">返库步骤已创建。请关闭弹窗，在单据卡片查看进度或重试下发。</p>}
  {error&&<p className="wm-error" role="alert">{error}</p>}<button className="wm-primary wm-full" disabled={!ready}>{busy?'提交中…':'确认并下发返库'}</button>
 </form></Modal>;
}
