import React,{useState} from 'react';
import {ClipboardList} from 'lucide-react';
import {Modal} from './components';
import {usePagination,useViewport} from './paging';
import Pager from './Pager';

export default function DraftTray({rows,title,submitLabel,onSubmit,onRemove,busy,canEdit,renderRow}){
 const [open,setOpen]=useState(false);
 const {height}=useViewport();
 const paging=usePagination(rows,Math.max(2,Math.min(6,Math.floor((height-290)/65))));
 if(!rows.length)return null;
 return <><div className="wm-draft-summary"><span><ClipboardList size={18}/><b>{title}</b> · {rows.length} 条</span><button type="button" onClick={()=>setOpen(true)}>查看清单</button><button type="button" className="wm-primary" disabled={!canEdit||busy} onClick={onSubmit}>{busy?'提交中…':`提交 ${rows.length} 条`}</button></div>
  {open&&<Modal title={`${title} · ${rows.length} 条`} onClose={()=>setOpen(false)}><p className="wm-help">整批核对通过后才登记，不会自动下发。</p><div className="wm-draft-list">{paging.items.map((row,index)=><div className="wm-draft-row" key={row.request_id}><span>{paging.page*paging.size+index+1}. {renderRow(row)}</span><button type="button" disabled={busy} aria-label={`移除清单 ${paging.page*paging.size+index+1}`} onClick={()=>onRemove(row)}>移除</button></div>)}</div><Pager paging={paging} label="清单"/><button type="button" className="wm-primary wm-full" disabled={!canEdit||busy} onClick={async()=>{await onSubmit();}}>{busy?'提交中…':submitLabel}</button></Modal>}
 </>;
}
