import React,{useState} from 'react';
import {Plus,Search,Download} from 'lucide-react';
import {apiPath,stockNames,storedLocation} from './api';
import {Empty,Modal} from './components';
import {materialInventory} from './materialSearch';
import {usePagination,useViewport} from './paging';
import Pager from './Pager';

export default function MaterialInventory({state,canEdit,onStocktake,onViewBox,onCount}){
 const [query,setQuery]=useState(''),[selected,setSelected]=useState(null);
 const {height}=useViewport(),all=materialInventory(state.stock);
 const terms=query.toLowerCase().trim().split(/\s+/).filter(Boolean);
 const items=all.filter(item=>{const text=[item.sku,item.material_name,item.model,item.specification,...item.boxes.map(box=>`${box.barcode} ${storedLocation(box.location)}`)].join(' ').toLowerCase();return terms.every(term=>text.includes(term));});
 const paging=usePagination(items,10,query);
 const material=all.find(item=>item.sku===selected),boxes=usePagination(material?.boxes||[],Math.max(1,Math.min(4,Math.floor((height-310)/64))),selected);
 return <><div className="wm-section-tools"><div className="wm-search"><Search size={18}/><input aria-label="搜索物料库存" placeholder="编码、名称、型号、规格或库位" value={query} onChange={event=>setQuery(event.target.value)}/></div><div className="wm-tool-buttons"><a className="wm-button" href={apiPath('/api/inventory.csv')}><Download size={18}/>导出库存明细</a><button disabled={!canEdit} onClick={onCount}>盘点单</button><button className="wm-primary" disabled={!canEdit} onClick={onStocktake}><Plus size={18}/>登记已有库存</button></div></div><div className="wm-list-summary"><span>按物料编码汇总 · {items.length} 种物料 · 账面数量 {items.reduce((sum,item)=>sum+item.quantity,0)}</span><span>含交接作业中的物料，正常返库或交接后更新</span></div>
  <section className="wm-panel wm-data-panel"><div className="wm-table-wrap"><table className="wm-material-inventory-table"><colgroup>{['19%','23%','17%','18%','15%','8%'].map((width,index)=><col key={index} style={{width}}/>)}</colgroup><thead><tr>{['物料编码','物料名称','型号','规格','账面数量','操作'].map(label=><th key={label}>{label}</th>)}</tr></thead><tbody>{paging.items.map(item=><tr key={item.sku}><td><strong>{item.sku}</strong></td><td>{item.material_name||'—'}</td><td>{item.model||'—'}</td><td>{item.specification||'—'}</td><td><strong>{item.quantity}</strong></td><td><button className="wm-table-action" aria-label={`查看物料库存 ${item.sku}`} onClick={()=>setSelected(item.sku)}>详情</button></td></tr>)}</tbody></table></div>{!items.length&&<Empty title="暂无匹配的物料库存">物料入库完成后按编码汇总显示；物料资料可在“物料档案”导入和查询。</Empty>}<Pager paging={paging} label="物料库存"/></section>
  {material&&<Modal title="物料库存详情" onClose={()=>setSelected(null)}><div className="wm-review"><p><b>{material.sku} · {material.material_name||'未填写名称'}</b></p><p>型号 {material.model||'—'} · 规格 {material.specification||'—'}</p><p>账面数量 {material.quantity} · 在库 {material.inStock} · 交接作业中 {material.working}</p><p>料箱数量 {material.boxes.length} 箱 · 任务预留 {material.reserved} 箱</p></div><table className="wm-material-box-table"><thead><tr><th>料箱</th><th>库位</th><th>数量</th><th>状态</th></tr></thead><tbody>{boxes.items.map(box=><tr key={box.barcode}><td><button type="button" className="wm-row-link" aria-label={`查看料箱 ${box.barcode}`} onClick={()=>{setSelected(null);onViewBox(box.barcode);}}>{box.barcode}</button></td><td>{storedLocation(box.location)}</td><td>{box.quantity}</td><td>{stockNames[box.status]}{box.reserved&&<small>任务预留</small>}</td></tr>)}</tbody></table><Pager paging={boxes} label="物料料箱"/></Modal>}
 </>;
}
