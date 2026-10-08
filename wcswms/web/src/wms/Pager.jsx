import React from 'react';
import {ChevronLeft,ChevronRight} from 'lucide-react';

export default function Pager({paging,label='记录'}){
 if(!paging.total)return null;
 const {page,pages,total,size,setPage}=paging;
 return <nav className="wm-pagination" aria-label={`${label}分页`}>
  <span className="wm-page-range">{page*size+1}—{Math.min((page+1)*size,total)} / {total} 项</span>
  <div><button type="button" aria-label={`${label}上一页`} disabled={page===0} onClick={()=>setPage(page-1)}><ChevronLeft size={17}/></button>
   <select aria-label={`${label}页码`} value={page} onChange={event=>setPage(Number(event.target.value))}>{Array.from({length:pages},(_,index)=><option key={index} value={index}>第 {index+1} / {pages} 页</option>)}</select>
   <button type="button" aria-label={`${label}下一页`} disabled={page===pages-1} onClick={()=>setPage(page+1)}><ChevronRight size={17}/></button></div>
 </nav>;
}
