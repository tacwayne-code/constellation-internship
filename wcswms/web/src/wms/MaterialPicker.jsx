import React,{useEffect,useRef,useState} from 'react';
import {api} from './api';
import {findMaterials} from './materialSearch';
import {usePagination,useViewport} from './paging';
import Pager from './Pager';

export default function MaterialPicker({value,selectedSku,onChange,onSelect,inputRef,disabled}){
 const [catalog,setCatalog]=useState([]),[open,setOpen]=useState(false),[active,setActive]=useState(-1),[error,setError]=useState(''),[tick,setTick]=useState(0),[loading,setLoading]=useState(true),[requested,setRequested]=useState(null);
 const root=useRef(null),{height}=useViewport();
 useEffect(()=>{const controller=new AbortController();setLoading(true);setError('');api('/api/materials',undefined,{signal:controller.signal}).then(data=>{setCatalog(data.materials);setError('');}).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});return()=>controller.abort();},[tick]);
 const matches=findMaterials(catalog,value),paging=usePagination(matches,height<650?2:3,value);
 const visible=open&&Boolean(value.trim())&&!disabled;
 useEffect(()=>setActive(-1),[value,paging.page]);
 useEffect(()=>{if(selectedSku)setOpen(false);},[selectedSku]);
 useEffect(()=>{if(!loading&&requested!==null){setRequested(null);if(requested===value.trim())confirm();}},[loading,requested,catalog,value]);
 function select(item){setOpen(false);setActive(-1);onSelect(item.sku,item);}
 function confirm(){const code=value.trim();if(!code||code.length>80)return;if(loading){setRequested(code);return;}const exact=catalog.find(item=>item.sku===code);if(exact)select(exact);else if(!matches.length&&!error){setOpen(false);onSelect(code,null);}}
 function key(event){
  if(event.nativeEvent.isComposing)return;
  if(event.key==='Escape'){setOpen(false);return;}
  if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();setOpen(true);setActive(index=>Math.max(0,Math.min(paging.items.length-1,index+(event.key==='ArrowDown'?1:-1))));}
  if(event.key==='Enter'){event.preventDefault();if(visible&&active>=0&&paging.items[active])select(paging.items[active]);else confirm();}
 }
 return <div className="wm-material-picker" ref={root} onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget)){setOpen(false);if(!selectedSku&&value.trim())confirm();}}}>
  <label>物料编码 / 模糊查找<input ref={inputRef} autoFocus autoComplete="off" spellCheck={false} role="combobox" aria-label="物料编码 / 模糊查找" aria-autocomplete="list" aria-expanded={visible} aria-controls={visible&&!error?'wm-material-options':undefined} aria-activedescendant={visible&&paging.items[active]?`wm-material-option-${active}`:undefined} value={value} disabled={disabled} maxLength={200} placeholder="编码、名称、型号或规格；扫码后回车" onFocus={()=>setOpen(true)} onChange={event=>{onChange(event.target.value);setOpen(true);}} onKeyDown={key}/></label>
  {visible&&<div className="wm-material-suggestions"><div className="wm-material-suggestions-head">{loading?'正在读取可选物料…':error?'档案读取失败':matches.length?`匹配 ${matches.length} 个物料，请选择`:'暂无匹配档案，可使用新编码'}</div>{error?<p className="wm-error">{error}<button type="button" onClick={()=>setTick(old=>old+1)}>重试</button></p>:<><div id="wm-material-options" role="listbox" aria-label="可选物料">{paging.items.map((item,index)=><button type="button" role="option" id={`wm-material-option-${index}`} aria-selected={active===index} key={item.sku} onMouseDown={event=>event.preventDefault()} onClick={()=>select(item)}><strong>{item.sku} · {item.material_name||'未填写名称'}</strong><small>型号 {item.model||'—'} · 规格 {item.specification||'—'}</small></button>)}</div><Pager paging={paging} label="可选物料"/>{!catalog.some(item=>item.sku===value.trim())&&<button type="button" className="wm-link" disabled={loading||!value.trim()||value.trim().length>80} onMouseDown={event=>event.preventDefault()} onClick={()=>{setOpen(false);onSelect(value.trim(),null);}}>使用新物料编码：{value.trim()}</button>}</>}</div>}
 </div>;
}
