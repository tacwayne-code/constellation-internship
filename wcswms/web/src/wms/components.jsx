import React,{useEffect,useRef,useState} from 'react';
import {X,Inbox,ExternalLink} from 'lucide-react';
import {taskNames,kindNames,formatTime,storedLocation,taskLabel,boxName} from './api';
import {materialFields} from './material';
import {boxContents,orderLines} from './material';
import {usePagination,useViewport} from './paging';
import Pager from './Pager';

export function BoxContents({item,onEdit,pageSize=3}){
 const rows=boxContents(item),paging=usePagination(rows,pageSize,item.barcode||item.id);
 if(!rows.length)return <p className="wm-help">空箱 · 无物料</p>;
 return <div className="wm-content-lines"><table><thead><tr><th>物料 / 批次</th><th>数量</th>{onEdit&&<th>操作</th>}</tr></thead><tbody>{paging.items.map(row=><tr key={row.sku}><td><b>{row.sku} · {row.material_name||'—'}</b><small>{row.model||'—'} · {row.specification||'—'} · 批次 {row.batch||'—'}</small></td><td>{row.quantity}</td>{onEdit&&<td><button type="button" onClick={()=>onEdit(row)}>编辑</button></td>}</tr>)}</tbody></table><Pager paging={paging} label="箱内物料"/></div>;
}

export function OrderLines({order,compact=false}){
 const {height}=useViewport(),rows=orderLines(order),paging=usePagination(rows,compact||height<650?1:2,order.id);
 const quantityLabel=order.kind==='COUNT'?(order.count_confirmed_at?'实盘数量':'账面数量'):'本单数量';
 return <div className="wm-order-lines">{rows.length?<table className="wm-order-material-table" aria-label="单据物料明细"><colgroup><col/><col/><col/><col/></colgroup><thead><tr><th scope="col">物料编码 / 名称</th><th scope="col">型号 / 规格</th><th scope="col">批次</th><th scope="col">{quantityLabel}</th></tr></thead><tbody>{paging.items.map((line,index)=><tr key={`${line.sku}:${index}`}><td><b>{line.sku||'—'}</b><small>{line.material_name||'—'}</small></td><td><span><em>型号</em>{line.model||'—'}</span><span><em>规格</em>{line.specification||'—'}</span></td><td>{line.batch||'—'}</td><td><b>{line.quantity??'—'}</b><small>账面变动<br/>{line.before_quantity??'—'} → {line.after_quantity??'—'}</small></td></tr>)}</tbody></table>:<p className="wm-help">空箱 · 无物料</p>}<Pager paging={paging} label="单据物料"/></div>;
}

export function MaterialFields({value,onChange,disabled=false}){
 return <>{materialFields.map(([key,label,maxLength])=><label className={key==='specification'?'wm-field-wide':undefined} key={key}>{label}<input disabled={disabled} value={value[key]||''} maxLength={maxLength} placeholder={`请输入${label}（选填）`} onChange={event=>onChange(key,event.target.value)}/></label>)}</>;
}

export function MaterialRows({item}){
 return <>{materialFields.map(([key,label])=><div key={key}><dt>{label}</dt><dd>{item[key]||'—'}</dd></div>)}</>;
}

export function ScanHint({value=''}){
 return <p className="wm-help wm-scan-hint"><span>英文输入法（英 / EN） · 扫码后回车</span><span>已接收 <b>{value.length}</b> 位</span></p>;
}

export function Modal({title,children,onClose,className}){
 const ref=useRef(null);
 useEffect(()=>{ref.current.showModal();},[]);
 return <dialog ref={ref} className={className} aria-label={title} onCancel={event=>{event.preventDefault();onClose();}}><div className="wm-dialog-head"><h2>{title}</h2><button type="button" className="wm-icon" aria-label="关闭弹窗" onClick={onClose}><X size={22}/></button></div><div className="wm-dialog-content">{children}</div></dialog>;
}
export function Empty({title='暂无记录',children}){return <div className="wm-empty"><Inbox size={48}/><strong>{title}</strong>{children&&<p>{children}</p>}</div>;}
export function Badge({status}){return <span className={`wm-badge wm-${status.toLowerCase()}`}>{taskNames[status]||status}</span>;}
export function ConfirmDialog({title,children,label='我已核对以上信息',button='确认',onClose,onConfirm,danger=false,disabled=false}){
 const [confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const busyRef=useRef(false);
 async function submit(event){event.preventDefault();if(!confirmed||busyRef.current||disabled)return;busyRef.current=true;setBusy(true);try{await onConfirm();onClose();}catch(e){setError(e.message);}finally{busyRef.current=false;setBusy(false);}}
 return <Modal title={title} onClose={onClose}><form onSubmit={submit}>{children}<label className="wm-check"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{label}</label>{error&&<p className="wm-error" role="alert">{error}</p>}<button className={`${danger?'wm-danger':'wm-primary'} wm-full`} disabled={!confirmed||busy||disabled}>{busy?'处理中…':button}</button></form></Modal>;
}
export function PlcSummary({state,onOpen}){
 const plc=state?.plc,live=plc?.live;
 return <section className="wm-panel wm-plc-summary"><div className="wm-panel-title"><h2>PLC 连接</h2><button className="wm-link" onClick={onOpen}>查看设备状态<ExternalLink size={15}/></button></div><dl><div><dt>CPU</dt><dd className={live?'wm-green':''}>{live?plc.cpu_state?.replace('S7CpuStatus','').toUpperCase():'离线'}</dd></div><div><dt>任务许可</dt><dd>{state?.readiness.ready?'可接单':'待就绪'}</dd></div><div><dt>地址</dt><dd>{plc?.endpoint?.replace(':102','')||'—'}</dd></div></dl></section>;
}
export function TaskTable({tasks,onOpen,title='最近作业'}){
 return <section className="wm-panel wm-recent"><div className="wm-panel-title"><h2>{title}</h2>{onOpen&&<button className="wm-link" onClick={onOpen}>全部任务 →</button>}</div><div className="wm-table-wrap"><table><thead><tr>{['任务号','类型','料箱','库位','状态','时间'].map(label=><th key={label}>{label}</th>)}</tr></thead><tbody>{tasks.map(task=><tr key={task.id}><td>{task.number}</td><td>{taskLabel(task)}</td><td>{task.barcode}</td><td>{storedLocation(task.location)}</td><td><Badge status={task.status}/></td><td>{formatTime(task.updated_at)}</td></tr>)}</tbody></table></div>{!tasks.length&&<Empty title="暂无作业记录">创建任务后将在此显示进度</Empty>}</section>;
}
