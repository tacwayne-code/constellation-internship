import React,{useMemo,useState} from 'react';
import {activeTask,locationLabel,storedLocation,boxName} from './api';

const PAGE_SIZE=8;
export default function OutboundChoices({query,state,busy,onSelect,containerType='MATERIAL',excluded=[]}){
 const [paging,setPaging]=useState({query:'',page:0});
 const result=useMemo(()=>{
  const locations=new Map(state.locations.map(location=>[location.id,location]));
  const reserved=new Set(state.tasks.filter(activeTask).map(task=>task.barcode));
  const terms=query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if(!terms.length&&containerType!=='EMPTY_BIN')return [];
  return state.stock.filter(item=>{
   const location=locations.get(item.location);
   if((item.container_type||'MATERIAL')!==containerType||excluded.includes(item.barcode)||item.status!=='IN_STOCK'||item.reserved||reserved.has(item.barcode)||!location?.enabled||location.reserved)return false;
   const text=[item.barcode,item.sku,item.material_name,item.model,item.specification,item.batch,storedLocation(item.location)].join(' ').toLowerCase();
   return terms.every(term=>text.includes(term));
  }).map(item=>({...item,position:locationLabel(locations.get(item.location))}));
 },[query,state.stock,state.locations,state.tasks,containerType,excluded]);
 if(!query.trim()&&containerType!=='EMPTY_BIN')return null;
 const page=Math.min(paging.query===query?paging.page:0,Math.max(0,Math.ceil(result.length/PAGE_SIZE)-1));
 return <section className="wm-outbound-choices" aria-label="可选出库库存"><div className="wm-choice-heading"><h3>可选料箱</h3><span>{result.length} 项匹配</span></div>
  <p className="wm-help">仅列出库位已启用且未被任务预留的在库料箱。点击一项带出物料信息。</p>
  {result.length?<div className="wm-choice-list">{result.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE).map(item=><button type="button" key={item.barcode} className="wm-stock-choice" disabled={busy} onClick={()=>onSelect(item)} aria-label={`选择料箱 ${item.barcode} ${item.container_type==='EMPTY_BIN'?'空箱':item.material_name||item.sku}`}><strong>{item.container_type==='EMPTY_BIN'?'空箱':item.material_name||item.sku}<span>{item.container_type==='EMPTY_BIN'?'1 箱':`数量 ${item.quantity}`}</span></strong><span>条码 {item.barcode} · {boxName(item)} {item.sku}</span><span>型号 {item.model||'—'} · 规格 {item.specification||'—'}</span><small>{item.position} · 批次 {item.batch||'—'}</small></button>)}</div>:<p className="wm-help" role="status">没有匹配的可出库库存，请更换关键词，或核对库存及库位状态。</p>}
  {result.length>PAGE_SIZE&&<div className="wm-choice-paging"><button type="button" disabled={page===0||busy} onClick={()=>setPaging({query,page:page-1})}>上一页</button><span>{page+1} / {Math.ceil(result.length/PAGE_SIZE)}</span><button type="button" disabled={(page+1)*PAGE_SIZE>=result.length||busy} onClick={()=>setPaging({query,page:page+1})}>下一页</button></div>}
 </section>;
}
