import React,{useState} from 'react';
import {activeTask,formatTime,storedLocation,taskLabel,boxName,kindNames} from './api';
import {Badge,Empty,MaterialRows} from './components';
import {TaskButtons,TaskActionDialog} from './TaskActions';
import OrderCards from './OrderCards';
import {usePagination,useTableSize,useViewport} from './paging';
import Pager from './Pager';

const orderStages={QUEUED:'待取箱',FETCHING:'取箱中',FETCH_ACK:'取箱回执处理中',WAIT_LOAD:'待人工装料',WAIT_PICK:'待扫码取料',RETURN_QUEUED:'待返库下发',RETURNING:'返库中',RETURN_ACK:'返库回执处理中',REVIEW:'待现场核对',COMPLETED:'已完成',CANCELLED:'已取消'};
export default function TaskPage({state,canEdit,refresh,notify}){
 const [filter,setFilter]=useState('active'),[type,setType]=useState('orders'),[selected,setSelected]=useState(null),[action,setAction]=useState(null);
 const {width}=useViewport();
 const orders=(state.orders||[]).filter(order=>filter==='all'||!['COMPLETED','CANCELLED'].includes(order.status));
 const tasks=state.tasks.filter(task=>!task.order_id&&(filter==='all'||activeTask(task)));
 const rows=type==='orders'?orders:tasks;
 const paging=usePagination(rows,useTableSize(),`${type}:${filter}`);
 const item=rows.find(row=>row.id===selected)||(width>=760?paging.items[0]:null);
 function chooseType(value){setType(value);setSelected(null);}
 return <><div className="wm-section-tools"><div className="wm-tabs" aria-label="任务范围"><button className={filter==='active'?'selected':''} onClick={()=>{setFilter('active');setSelected(null);}}>待处理</button><button className={filter==='all'?'selected':''} onClick={()=>{setFilter('all');setSelected(null);}}>全部任务</button></div><span className="wm-help">物料数量在返库完成后更新；PLC 正常回执自动确认。</span></div>
  <div className={`wm-task-board ${item?'has-selection':''}`}><section className="wm-panel wm-data-panel wm-task-table-panel"><div className="wm-panel-title"><div className="wm-tabs" aria-label="任务分类"><button className={type==='orders'?'selected':''} onClick={()=>chooseType('orders')}>物料单据 · {orders.length}</button><button className={type==='tasks'?'selected':''} onClick={()=>chooseType('tasks')}>料箱任务 · {tasks.length}</button></div></div><div className="wm-table-wrap"><table className="wm-task-table"><colgroup><col style={{width:'28%'}}/><col style={{width:'17%'}}/><col style={{width:'33%'}}/><col style={{width:'22%'}}/></colgroup><thead><tr><th>单据 / 任务号</th><th>类型</th><th>料箱</th><th>状态</th></tr></thead><tbody>{paging.items.map(row=><tr key={row.id} className={item?.id===row.id?'wm-row-selected':''}><td><button className="wm-row-link" onClick={()=>setSelected(row.id)} aria-label={`查看任务 ${row.number}`}>{row.number}</button><small>{formatTime(row.updated_at)}</small></td><td>{type==='orders'?`${kindNames[row.kind]}单`:taskLabel(row)}</td><td>{row.barcode}<small>{storedLocation(row.location)}</small></td><td>{type==='orders'?<span className={`wm-badge wm-${row.status.toLowerCase()}`}>{orderStages[row.status]||row.status}</span>:<Badge status={row.status}/>}</td></tr>)}</tbody></table></div>{!rows.length&&<Empty title="暂无待处理记录">切换“全部任务”可查看历史记录</Empty>}<Pager paging={{...paging,setPage:page=>{paging.setPage(page);setSelected(null);}}} label="任务"/></section>
   <section className="wm-panel wm-task-detail"><div className="wm-panel-title"><h2>{type==='orders'?'单据详情':'任务详情'}</h2><button className="wm-link wm-mobile-toggle" onClick={()=>setSelected(null)}>返回列表</button></div>{!item?<Empty title="选择一条记录">查看完整信息、执行进度与可用操作</Empty>:type==='orders'?<OrderCards {...{state,canEdit,refresh,notify}} all onlyId={item.id}/>:<><div className="wm-task-heading"><div><h3>{item.barcode}</h3><p className="wm-help">{taskLabel(item)} · {item.number}</p></div><Badge status={item.status}/></div><dl className="wm-info-list wm-detail-grid"><div><dt>库位</dt><dd>{storedLocation(item.location)}</dd></div><div><dt>料箱类型</dt><dd>{boxName(item)}</dd></div>{item.container_type!=='EMPTY_BIN'&&<><div><dt>物料编码</dt><dd>{item.sku}</dd></div><MaterialRows item={item}/><div><dt>数量</dt><dd>{item.quantity}</dd></div></>}<div><dt>PLC 任务号</dt><dd>{item.plc_id??'下发时分配'}</dd></div><div><dt>更新时间</dt><dd>{formatTime(item.updated_at)}</dd></div></dl><p className="wm-review">{item.detail}</p><TaskButtons task={item} state={state} canEdit={canEdit} onAction={kind=>setAction({task:item,kind})}/></>}</section>
  </div>{action&&<TaskActionDialog action={action} state={state} canEdit={canEdit} refresh={refresh} notify={notify} onClose={()=>setAction(null)}/>}
 </>;
}
