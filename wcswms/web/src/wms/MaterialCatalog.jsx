import React,{useEffect,useRef,useState} from 'react';
import {Upload,Download,Search,ArrowLeft} from 'lucide-react';
import {api,apiPath,requestId} from './api';
import {Empty,Modal} from './components';
import Pager from './Pager';
import {usePagination,useTableSize,useViewport} from './paging';

const statuses={NEW:'新增',FILL:'补全空字段',UNCHANGED:'相同跳过',ERROR:'需修正'};
function readFile(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({filename:file.name,content:String(reader.result).split(',')[1]});reader.onerror=()=>reject(new Error('文件读取失败，请重新选择'));reader.readAsDataURL(file);});}

export default function MaterialCatalog({canEdit,refresh,notify}){
 const [data,setData]=useState(null),[query,setQuery]=useState(''),[file,setFile]=useState(null),[preview,setPreview]=useState(null),[onlyErrors,setOnlyErrors]=useState(false),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const picker=useRef(null),busyRef=useRef(false),alive=useRef(true),id=useRef(requestId());
 const [details,setDetails]=useState(null);
 useEffect(()=>{alive.current=true;const controller=new AbortController();api('/api/materials',undefined,{signal:controller.signal}).then(value=>setData(value.materials)).catch(e=>{if(!controller.signal.aborted)setError(e.message);});return()=>{alive.current=false;controller.abort();};},[]);
 const records=preview?preview.rows.filter(row=>!onlyErrors||row.status==='ERROR'):(data||[]).filter(row=>[row.sku,row.material_name,row.model,row.specification].join(' ').toLowerCase().includes(query.toLowerCase()));
 const {height}=useViewport();
 const paging=usePagination(records,Math.max(1,useTableSize()-(height<560?2:1)),`${preview?.token||'catalog'}:${query}:${onlyErrors}`);
 function reset(){setFile(null);setPreview(null);setDetails(null);setError('');setOnlyErrors(false);setConfirmed(false);id.current=requestId();}
 async function choose(event){
  const selected=event.target.files?.[0];event.target.value='';if(!selected||busyRef.current||!canEdit)return;
  reset();if(selected.size>2*1024*1024){setError('文件不能超过 2 MB');return;}
  busyRef.current=true;setBusy(true);
  try{const value=await readFile(selected);if(!alive.current)return;const result=await api('/api/materials/import/preview',value);if(alive.current){setFile(value);setPreview(result);}}
  catch(e){if(alive.current)setError(e.message);}finally{busyRef.current=false;if(alive.current)setBusy(false);}
 }
 async function submit(){
  if(busyRef.current||!canEdit||!confirmed||!preview?.valid||!file)return;
  busyRef.current=true;setBusy(true);setError('');
  try{
   const result=await api('/api/materials/import',{...file,request_id:id.current,expected_token:preview.token,confirmed:true});
   if(!alive.current)return;
   reset();notify(`物料导入完成：新增 ${result.counts.NEW}，补全 ${result.counts.FILL}，跳过 ${result.counts.UNCHANGED}`);
   const [catalog]=await Promise.all([api('/api/materials'),refresh()]);if(alive.current)setData(catalog.materials);
  }catch(e){if(alive.current){setError(e.message);if(preview&&e.status===409)setConfirmed(false);}}finally{busyRef.current=false;if(alive.current)setBusy(false);}
 }
 return <div className="wm-material-catalog">
  <div className="wm-section-tools">
   {preview?<button disabled={busy} onClick={reset}><ArrowLeft size={17}/>返回档案</button>:<div className="wm-search"><Search size={18}/><input aria-label="搜索物料档案" placeholder="搜索编码、名称、型号或规格" value={query} onChange={e=>setQuery(e.target.value)}/></div>}
   <div className="wm-tool-buttons"><a className="wm-button" href={apiPath('/api/materials/template.xlsx')}><Download size={17}/>下载模板</a><button className="wm-primary" disabled={!canEdit||busy} onClick={()=>picker.current?.click()}><Upload size={17}/>{busy?'处理中…':preview?'重新选择文件':'导入物料信息'}</button></div>
   <input ref={picker} type="file" accept=".xlsx,.csv" hidden onChange={choose}/>
  </div>
  <p className="wm-help">导入到当前设备的物料档案。支持 XLSX（首个工作表）或 CSV，最多 1000 条 / 2 MB；编码列请设为文本。库存数量与库位保持不变。</p>
  {error&&<p className="wm-error" role="alert">{error}</p>}
  {preview?<div className="wm-material-import-summary"><span><b>{file.filename}</b> · {preview.total} 条：新增 {preview.counts.NEW} / 补全 {preview.counts.FILL} / 跳过 {preview.counts.UNCHANGED} / 错误 {preview.counts.ERROR}</span>{preview.counts.ERROR>0&&<label className="wm-check"><input type="checkbox" checked={onlyErrors} onChange={e=>setOnlyErrors(e.target.checked)}/>只看错误行</label>}</div>:<p className="wm-help">{data?.length||0} 个物料编码 · 即使没有在库料箱，档案仍保留</p>}
  <section className="wm-panel wm-data-panel"><div className="wm-table-wrap"><table className="wm-materials-table"><colgroup>{(preview?['7%','18%','21%','16%','23%','15%']:['22%','28%','22%','28%']).map((width,index)=><col key={index} style={{width}}/>)}</colgroup><thead><tr>{(preview?['行','物料编码','物料名称','型号','规格','校验结果']:['物料编码','物料名称','型号','规格']).map(label=><th key={label}>{label}</th>)}</tr></thead><tbody>{paging.items.map(row=><tr key={preview?row.row:row.sku}>{preview&&<td>{row.row}</td>}{['sku','material_name','model','specification'].map(key=><td key={key}>{key==='sku'?<button type="button" className="wm-row-link wm-material-cell" title={row.sku} aria-label={`查看物料 ${row.sku}`} onClick={()=>setDetails(row)}>{row.sku||'—'}</button>:<span className="wm-material-cell" title={row[key]||'—'}>{row[key]||'—'}</span>}</td>)}{preview&&<td><button type="button" className="wm-table-action" aria-label={`查看第 ${row.row} 行校验详情`} onClick={()=>setDetails(row)}>{statuses[row.status]}</button>{row.errors.length>0&&<span className="wm-material-cell wm-error" title={row.errors.join('；')}>{row.errors.join('；')}</span>}</td>}</tr>)}</tbody></table></div>{!records.length&&<Empty title={data===null&&!preview?'正在读取物料档案…':preview?'没有错误行':'暂无匹配的物料档案'}>通过模板导入后，登记入库时扫描编码可自动带出物料信息。</Empty>}<Pager paging={paging} label={preview?'导入预览':'物料档案'}/></section>
  {preview&&<div className="wm-material-import-actions"><label className="wm-check"><input type="checkbox" checked={confirmed} disabled={!preview.valid||busy||!canEdit} onChange={e=>setConfirmed(e.target.checked)}/>{preview.valid?'已核对：仅新增编码、补全空字段，不覆盖已有信息':'有错误行，整批不会导入；请修正后重新选择文件'}</label><button className="wm-primary" disabled={!canEdit||busy||!preview.valid||!confirmed} onClick={submit}>{busy?'导入中…':'确认导入'}</button></div>}
  {details&&<Modal title={preview?`第 ${details.row} 行校验详情`:'物料档案详情'} onClose={()=>setDetails(null)}><dl className="wm-info-list">{[["物料编码",details.sku],["物料名称",details.material_name],["型号",details.model],["规格",details.specification]].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value||"—"}</dd></div>)}</dl><p className={details.errors?.length?"wm-error":"wm-help"}>{details.errors?.join("；")||statuses[details.status]||'物料长期档案'}</p></Modal>}
 </div>;
}
