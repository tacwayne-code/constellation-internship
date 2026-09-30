import React,{useState} from 'react';
import {api,requestId} from './api';
import {Modal} from './components';
import {policyValues,priorities,directions} from './allocation';

export default function AllocationSettings({policy,onClose,onDone}){
 const [form,setForm]=useState(()=>({request_id:requestId(),allocation:policyValues(policy),expected_revision:policy?.revision??0}));
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const stale=form.expected_revision!==(policy?.revision??0);
 const update=(key,value)=>setForm(old=>({...old,allocation:{...old.allocation,[key]:value},request_id:requestId()}));
 async function submit(event){event.preventDefault();if(busy||stale)return;setBusy(true);setError('');try{await api('/api/allocation-policy',form);await onDone();onClose();}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <Modal title="分配规则" onClose={onClose} className="wm-allocation-dialog"><form onSubmit={submit}>
  <p className="wm-help">左右仓共用，随时可改。保存后立即用于新建任务，已有任务和返库原位保持不变。</p>
  <label>左右分配方式<select disabled={busy} value={form.allocation.side_order} onChange={e=>update('side_order',e.target.value)}>{Object.entries(directions.side_order).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
  <p className="wm-review">{form.allocation.side_order==='BALANCED'?'均分按左、右、左、右交替分配，切换到均分时从左仓开始。某侧没有可用候选时使用另一侧，下次继续优先分配其对侧。':form.allocation.side_order==='RIGHT_FIRST'?'先分配右仓的可用库位，右仓无可用库位后再分配左仓。':'先分配左仓的可用库位，左仓无可用库位后再分配右仓。'}</p>
  <h3>仓内顺序</h3><label>仓内优先级<select disabled={busy} value={form.allocation.priority} onChange={e=>update('priority',e.target.value)}>{Object.entries(priorities).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
  <label>上下顺序<select disabled={busy} value={form.allocation.level_order} onChange={e=>update('level_order',e.target.value)}>{Object.entries(directions.level_order).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
  <label>前后顺序<select disabled={busy} value={form.allocation.column_order} onChange={e=>update('column_order',e.target.value)}>{Object.entries(directions.column_order).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
  <p className="wm-help">先选左右仓，再按仓内顺序分配。前＝小列号，后＝大列号。自动选空位和自动配空箱均按本规则；均分进度在重启后接续。停用、未核对、占用、预留及双伸通道受阻的候选仍会跳过。</p>
  {stale&&<p className="wm-error" role="alert">规则已被其他终端修改，请关闭后重新打开。</p>}{error&&<p className="wm-error" role="alert">{error}</p>}
  <button className="wm-primary wm-full" disabled={busy||stale}>{busy?'保存中…':'保存分配规则'}</button>
 </form></Modal>;
}
