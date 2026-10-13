import React,{useState} from 'react';
import {api,requestId} from './api';

export default function OutboundScanSettings({state,refresh,notify,online}){
 const policy=state.outbound_scan_policy||{require_barcode:true,require_sku:true,revision:0};
 const [form,setForm]=useState(()=>({...policy,request_id:requestId()})),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const stale=form.revision!==policy.revision;
 const changed=form.require_barcode!==policy.require_barcode||form.require_sku!==policy.require_sku;
 function update(key,value){setForm(old=>({...old,[key]:value,request_id:requestId()}));}
 async function submit(event){event.preventDefault();if(busy||stale||!changed||!online)return;setBusy(true);setError('');try{const saved=await api('/api/outbound-scan-policy',{request_id:form.request_id,require_barcode:form.require_barcode,require_sku:form.require_sku,expected_revision:form.revision});setForm({...saved,request_id:requestId()});await refresh();notify('当前设备的出库扫码设置已保存');}catch(e){setError(e.message);await refresh().catch(()=>{});}finally{setBusy(false);}}
 return <section className="wm-panel wm-scan-settings"><div className="wm-panel-title"><h2>出库扫码要求</h2><span className="wm-badge wm-completed">当前设备</span></div><p className="wm-help">用于物料出库单的取料核对与返库。每台设备独立保存，重启后保留；保存后对尚未提交返库的出库单生效。</p><form onSubmit={submit}><label className="wm-check"><input type="checkbox" checked={form.require_barcode} disabled={busy||!online} onChange={e=>update('require_barcode',e.target.checked)}/>箱子编码需要扫码</label><label className="wm-check"><input type="checkbox" checked={form.require_sku} disabled={busy||!online} onChange={e=>update('require_sku',e.target.checked)}/>物料编码需要扫码</label><p className="wm-review">关闭某项后，核对窗口无需输入对应编码。操作员仍需确认已按单据数量取料、料箱已就位，再下发返库。</p>{stale&&<p className="wm-error" role="alert">设置已被其他终端修改，请重新读取后编辑。</p>}{error&&<p className="wm-error" role="alert">{error}</p>}<div className="wm-form-actions"><button type="button" disabled={busy||!online} onClick={()=>{setForm({...policy,request_id:requestId()});setError('');}}>重新读取</button><button className="wm-primary" disabled={busy||stale||!changed||!online}>{busy?'保存中…':'保存扫码设置'}</button></div></form></section>;
}
