import React,{useState} from 'react';
import {activeTask,kindNames,formatTime,storedLocation,taskLabel,boxName} from './api';
import {Badge,Empty} from './components';
import {TaskButtons,TaskActionDialog} from './TaskActions';
import OrderCards from './OrderCards';
import {materialFields} from './material';

export default function TaskPage({state,canEdit,refresh,notify}){
 const [filter,setFilter]=useState('active'),[action,setAction]=useState(null);
 const tasks=state.tasks.filter(task=>!task.order_id&&(filter==='all'||activeTask(task)));
 return <><div className="wm-section-tools"><div className="wm-tabs"><button className={filter==='active'?'selected':''} onClick={()=>setFilter('active')}>待处理</button><button className={filter==='all'?'selected':''} onClick={()=>setFilter('all')}>全部任务</button></div><span className="wm-help">物料数量在返库完成后更新；PLC 正常回执自动确认。</span></div>
 <section className="wm-panel"><h2>物料单据</h2><OrderCards {...{state,canEdit,refresh,notify}} all={filter==='all'}/></section><section className="wm-task-list wm-spaced">{tasks.map(task=><article className="wm-panel wm-task" key={task.id}><div className="wm-task-heading"><div><h2>{task.barcode}<small>{taskLabel(task)} · {task.number}</small></h2></div><Badge status={task.status}/></div><div className="wm-task-meta"><span>库位 <b>{storedLocation(task.location)}</b></span><span>料箱类型 <b>{boxName(task)}</b></span>{task.container_type!=='EMPTY_BIN'&&<><span>物料编码 <b>{task.sku}</b></span>{materialFields.map(([key,label])=><span key={key}>{label} <b>{task[key]||'—'}</b></span>)}<span>数量 <b>{task.quantity}</b></span></>}<span>PLC 任务号 <b>{task.plc_id??'下发时分配'}</b></span><span>{formatTime(task.updated_at)}</span></div><p>{task.detail}</p><TaskButtons task={task} state={state} canEdit={canEdit} onAction={kind=>setAction({task,kind})}/></article>)}</section>{!tasks.length&&<section className="wm-panel"><Empty title="暂无待处理任务">扫码登记后，任务将在这里显示</Empty></section>}
 {action&&<TaskActionDialog action={action} state={state} canEdit={canEdit} refresh={refresh} notify={notify} onClose={()=>setAction(null)}/>}
 </>;
}
