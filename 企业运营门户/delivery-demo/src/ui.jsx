import React, {useEffect,useRef} from 'react';
import {X,ChevronRight} from 'lucide-react';
export function Badge({children,tone=''}) { return <span className={`badge ${tone || (['高','高风险','到货延迟'].includes(children)?'red':['中','待确认','交期临近','待发货'].includes(children)?'orange':'green')}`}>{children}</span>; }
export function Panel({title,action,onAction,children,className=''}) {return <section className={`panel ${className}`}><div className="panel-heading"><h2>{title}</h2>{action&&<button className="text-button" onClick={onAction}>{action}<ChevronRight size={16}/></button>}</div>{children}</section>;}
export function Empty({children='没有符合条件的记录'}) {return <div className="empty">{children}</div>;}
export function Modal({title,children,onClose,wide=false}) {
 const ref=useRef(null);
 useEffect(()=>{const before=document.activeElement; const dialog=ref.current;dialog.showModal();const handle=e=>{if(e.key==='Escape'){e.preventDefault();onClose();}};dialog.addEventListener('keydown',handle);return()=>{dialog.removeEventListener('keydown',handle);dialog.close();before?.focus();};},[onClose]);
 return <dialog ref={ref} className={wide?'wide':''} onClick={e=>{if(e.target===ref.current)onClose();}}><div className="modal-head"><h2>{title}</h2><button aria-label="关闭" className="icon-button" onClick={onClose}><X/></button></div><div className="modal-body">{children}</div></dialog>;
}
