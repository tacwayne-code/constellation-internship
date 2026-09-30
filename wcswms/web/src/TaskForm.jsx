import React,{useEffect,useRef,useState} from 'react';
import {X,ArrowRight} from 'lucide-react';
import {kinds} from './api';

export default function TaskForm({state,onClose,onSubmit,initialTarget}){
  const dialog=useRef(null);
  const [form,setForm]=useState({request_id:crypto.randomUUID(),kind:'INBOUND',load_id:`BOX-${Date.now().toString().slice(-7)}`,target:initialTarget||'AUTO',sku:'SKU-001',quantity:1,weight_kg:10});
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{dialog.current.showModal();},[]);
  const available=Object.values(state.loads).filter(l=>state.locations[l.location]&&!state.locations[l.location].reserved);
  const empty=Object.values(state.locations).filter(l=>!l.load&&!l.reserved);
  function update(key,value){setForm(f=>({...f,[key]:value}));setError('');}
  function changeKind(kind){setForm(f=>({...f,kind,load_id:kind==='INBOUND'?`BOX-${Date.now().toString().slice(-7)}`:available[0]?.id||'',target:kind==='INBOUND'?'AUTO':empty[0]?.id||''}));}
  async function submit(e){e.preventDefault();setBusy(true);try{await onSubmit(form);onClose();}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <dialog ref={dialog} onCancel={onClose}><form onSubmit={submit}><div className="dialog-head"><div><h2>新建运输任务</h2><p>选择作业类型并预留目标库位</p></div><button type="button" className="icon-button" aria-label="关闭" onClick={onClose}><X size={20}/></button></div><div className="segmented">{Object.entries(kinds).map(([k,v])=><button key={k} type="button" className={form.kind===k?'selected':''} onClick={()=>changeKind(k)}>{v}</button>)}</div><label>载具编号{form.kind==='INBOUND'?<input autoFocus required pattern="[A-Za-z0-9_-]+" maxLength={64} value={form.load_id} onChange={e=>update('load_id',e.target.value)}/>:<select required value={form.load_id} onChange={e=>update('load_id',e.target.value)}>{available.length===0&&<option value="">暂无可用载具</option>}{available.map(l=><option key={l.id} value={l.id}>{l.id} · {l.location} · {l.sku}</option>)}</select>}</label>{form.kind==='INBOUND'&&<><label>物料编码<input required maxLength={64} value={form.sku} onChange={e=>update('sku',e.target.value)}/></label><div className="form-row"><label>数量<input type="number" required min="1" max="1000000" step="1" value={form.quantity} onChange={e=>update('quantity',Number(e.target.value))}/></label><label>重量（kg）<input type="number" required min="0.1" max="150" step="0.1" value={form.weight_kg} onChange={e=>update('weight_kg',Number(e.target.value))}/></label></div></>}
  <label>目标位置{form.kind==='OUTBOUND'?<input value="OUT · 出库站台" readOnly/>:<select required value={form.target} onChange={e=>update('target',e.target.value)}>{form.kind==='INBOUND'&&<option value="AUTO">自动分配空闲库位</option>}{empty.map(l=><option key={l.id} value={l.id}>{l.id} · {l.side==='L'?'左':'右'}侧 {l.column}列 {l.level}层</option>)}</select>}</label><div className="form-note">本次只执行本地仿真。源位置与目标位置将在创建时原子预留。</div>{error&&<p className="error-text" role="alert">{error}</p>}<div className="dialog-actions"><button type="button" onClick={onClose}>取消</button><button className="primary" disabled={busy||!form.load_id} type="submit">{busy?'正在创建…':'创建并入队'}<ArrowRight size={16}/></button></div></form></dialog>;
}
