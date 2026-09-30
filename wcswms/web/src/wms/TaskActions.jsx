import React,{useState} from 'react';
import {api,kindNames,storedLocation,taskLabel,boxName} from './api';
import {ConfirmDialog,Modal} from './components';
import {materialFields} from './material';

function restriction(task,kind,state,canEdit){
 if(!canEdit)return '请先解锁操作，并确认后台连接正常。';
 if(!task)return '任务已变化，请关闭窗口后重新核对。';
 const expected=kind==='acknowledge'?'PLC_DONE':'QUEUED';
 if(task.status!==expected)return '任务状态已变化，请关闭窗口后重新核对。';
 if(kind==='dispatch'){
  if(!state.locations.find(location=>location.id===task.location)?.enabled)return '任务库位已停用，不能下发。';
  if(state.tasks.some(other=>other.id!==task.id&&['DISPATCHING','SENT','RUNNING','REVIEW','PLC_DONE'].includes(other.status)))return '设备已有未结束任务或待确认回执，请先处理。';
  if(task.kind==='OUTBOUND'&&state.stock.some(item=>item.status==='AT_EXIT'))return '出口尚有待取料箱，请先扫码确认取走。';
  if(state.stock.some(item=>item.status==='AT_STATION'&&!(task.order_leg==='RETURN'&&item.reserved===task.id)))return '交接点正在装料或取料，请先完成该箱返库。';
  const location=state.locations.find(item=>item.id===task.location);
  if(task.kind==='INBOUND'?location?.inbound_access_error:location?.access_error)return (task.kind==='INBOUND'?location.inbound_access_error:location.access_error);
  if(!state.readiness.ready)return `设备待就绪：${state.readiness.reasons.join('；')}`;
 }
 if(kind==='acknowledge'&&!state.plc.live)return 'PLC 连接待就绪，暂不能确认回执。';
 return '';
}

export function TaskButtons({task,state,canEdit,onAction}){
 return <div className="wm-task-actions">
  {task.status==='QUEUED'&&<><button type="button" className="wm-primary" disabled={Boolean(restriction(task,'dispatch',state,canEdit))} onClick={()=>onAction('dispatch')}>{task.order_leg==='FETCH'?'核对取箱':task.order_leg==='RETURN'?'核对返库':'核对并下发'}</button><button type="button" disabled={!canEdit} onClick={()=>onAction('cancel')}>取消未下发任务</button>{canEdit&&restriction(task,'dispatch',state,canEdit)&&<span className="wm-help">{restriction(task,'dispatch',state,canEdit)}</span>}</>}
  {task.status==='PLC_DONE'&&(state.auto_acknowledge&&!task.ack_error?<span className="wm-help" role="status">后台正在自动确认 PLC 回执，无需人工操作。</span>:<button type="button" className="wm-primary" disabled={Boolean(restriction(task,'acknowledge',state,canEdit))} onClick={()=>onAction('acknowledge')}>{task.ack_error?'重试回执确认':'确认 PLC 回执'}</button>)}
  {task.status==='AWAIT_PICKUP'&&<span className="wm-help">在出库登记处扫描出口料箱，确认已取走。</span>}
  {task.status==='AWAIT_RETURN'&&<span className="wm-help">在物料单据中完成装料或扫码取料，再点击返库。</span>}
  {task.status==='REVIEW'&&<button type="button" disabled={!canEdit||!state.plc.live} onClick={()=>onAction('resolve')}>现场核对处理</button>}
 </div>;
}

export function TaskActionDialog({action,state,canEdit,refresh,notify,onClose}){
 const task=state.tasks.find(item=>item.id===action.task.id),kind=action.kind;
 if(kind==='resolve')return <Recovery task={action.task} onClose={onClose} onDone={async()=>{await refresh();notify('现场核对结果已记录');}}/>;
 const blocked=restriction(task,kind,state,canEdit);
 const order=state.orders?.find(item=>item.id===action.task.order_id);
 const cancelHelp=action.task.order_leg==='RETURN'?'仅取消尚未下发的返库搬运。料箱仍在交接点，原库位预留和物料单据保留，核对后可重新返库。':action.task.order_leg==='FETCH'?'取消这张物料单据，并释放料箱和原库位预留；库存数量保持不变。':'取消后释放本任务的库位、料箱预留；库存数量保持不变。';
 async function submit(){
  if(blocked)throw new Error(blocked);
  await api(`/api/tasks/${task.id}/${kind}`,kind==='cancel'?{}:{site_ready:true});
  await refresh();notify(kind==='dispatch'?'下发已处理，请依据 PLC 反馈查看进度':kind==='acknowledge'?'PLC 回执已确认':'任务已取消');
 }
 return <ConfirmDialog title={kind==='dispatch'?'向真机下发任务':kind==='acknowledge'?'确认 PLC 完成回执':'取消未下发任务'} label={kind==='dispatch'?'已核对料箱、库位、交接点和现场控制权':kind==='acknowledge'?'已核对设备完成，允许确认该任务回执':'确认取消此任务'} button={kind==='dispatch'?'确认下发至真机':kind==='acknowledge'?'确认回执并复归空闲':'确认取消'} disabled={Boolean(blocked)} onClose={onClose} onConfirm={submit}><div className="wm-review"><p><b>{taskLabel(action.task)} · {action.task.barcode}</b></p><p>任务：{action.task.number}</p><p>库位：{storedLocation(action.task.location)} / 码头：{action.task.dock}</p><p>料箱类型：{boxName(action.task)}</p>{action.task.container_type!=='EMPTY_BIN'&&<p>物料编码：{action.task.sku} / 数量：{action.task.quantity}</p>}{action.task.container_type!=='EMPTY_BIN'&&materialFields.map(([key,label])=><p key={key}>{label}：{action.task[key]||'—'}</p>)}{order&&<p>单据 {order.number}：{kindNames[order.kind]} {order.sku}，数量 {order.quantity}；返库后箱内数量 {order.after_quantity}</p>}</div>{kind==='cancel'&&<p className="wm-help">{cancelHelp}</p>}{blocked&&<p className="wm-error" role="alert">{blocked}</p>}</ConfirmDialog>;
}

function Recovery({task,onClose,onDone}){
 const [outcome,setOutcome]=useState('AT_SOURCE'),[note,setNote]=useState(''),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function submit(event){event.preventDefault();setBusy(true);try{await api(`/api/tasks/${task.id}/resolve`,{outcome,note,site_ready:confirmed});await onDone();onClose();}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <Modal title="现场核对处理" onClose={onClose}><form onSubmit={submit}><p className="wm-review">{task.number} · {task.barcode}</p><label>料箱实际位置<select value={outcome} onChange={e=>setOutcome(e.target.value)}><option value="AT_SOURCE">仍在本次搬运起点，取消本次搬运</option><option value="AT_DESTINATION">已到目标位置，按现场结果更新库存</option></select></label><label>核对说明<textarea minLength={4} maxLength={200} required value={note} onChange={e=>setNote(e.target.value)} placeholder="记录现场位置和核对人"/></label><label className="wm-check"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>设备已停止，已现场核对料箱实际位置</label>{error&&<p className="wm-error">{error}</p>}<button className="wm-primary wm-full" disabled={!confirmed||busy}>记录核对结果</button></form></Modal>;
}
