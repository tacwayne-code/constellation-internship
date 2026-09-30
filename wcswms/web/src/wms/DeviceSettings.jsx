import React,{useState} from 'react';
import {api} from './api';
import {Modal} from './components';

export default function DeviceSettings({settings,canEdit,onClose,onDone}){
 const [form,setForm]=useState(()=>({host:settings.host,expected_revision:settings.revision}));
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const stale=form.expected_revision!==settings.revision;
 async function submit(event){
  event.preventDefault();if(busy||stale||!canEdit)return;setBusy(true);setError('');
  try{const result=await api('/api/device/settings',form);await onDone(result.changed);onClose();}
  catch(e){setError(e.message);}finally{setBusy(false);}
 }
 return <Modal title="设备 IP 设置" onClose={()=>{if(!busy)onClose();}}><form onSubmit={submit}>
  <p className="wm-help">保存后立即按新地址重新连接，重启后台后仍保留。请填写与当前仓库对应的 PLC 地址。</p>
  <label>PLC IP 地址<input value={form.host} onChange={event=>setForm(old=>({...old,host:event.target.value}))} disabled={busy} autoFocus required inputMode="decimal" autoComplete="off" spellCheck={false} maxLength={15} placeholder="192.168.0.100"/></label>
  <p className="wm-review">端口：{settings.port} · 机架：{settings.rack} · 槽号：{settings.slot}</p>
  <p className="wm-help">正在执行任务、等待交接或返库时不能切换。保存后请查看设备采样状态是否恢复为“实时”；未连接时可再次修改地址。</p>
  {!canEdit&&<p className="wm-error" role="alert">请先解锁操作并确认后台在线。</p>}
  {stale&&<p className="wm-error" role="alert">设备 IP 已被其他终端修改，请关闭后重新打开。</p>}
  {error&&<p className="wm-error" role="alert">{error}</p>}
  <button className="wm-primary wm-full" disabled={busy||stale||!canEdit}>{busy?'保存中…':'保存并重连'}</button>
 </form></Modal>;
}
