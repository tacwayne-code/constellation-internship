import React,{lazy,Suspense,useEffect,useState} from 'react';
import {Box,ShieldCheck,Wifi,Activity} from 'lucide-react';
import PanelBoundary from './PanelBoundary';
import {api,time} from './api';
import {validPhysicalCalibration} from './twin-pose';
import './physical-twin.css';

const Twin=lazy(()=>import('./RealTwin'));
const statusLabels={1:'出库完成',2:'入库完成',3:'移库完成',4:'移动完成',5:'取箱完成',6:'放箱完成',7:'故障',8:'空闲',9:'执行中',10:'复位'};
const number=value=>Number.isFinite(value)?value.toFixed(3):'—';

export default function PhysicalTwin(){
 const [state,setState]=useState(null),[online,setOnline]=useState(true),[error,setError]=useState('');
 const [calibration,setCalibration]=useState(null),[calibrationError,setCalibrationError]=useState('');
 useEffect(()=>{
  const controller=new AbortController();
  api('/assets/structure/physical-calibration.json',undefined,{signal:controller.signal,cache:'no-store'})
   .then(value=>{if(!validPhysicalCalibration(value))throw new Error('显示标定配置格式错误');setCalibration(value);})
   .catch(e=>{if(e.name!=='AbortError')setCalibrationError(e.message);});
  return()=>controller.abort();
 },[]);
 useEffect(()=>{
  document.title='立库真机 · 只读三维展示';
  let stopped=false,timer,controller;
  async function poll(){
   controller=new AbortController();
   const timeout=setTimeout(()=>controller.abort(),5000);
   try{
    const data=await api('/api/state',undefined,{signal:controller.signal,cache:'no-store'});
    if(data.mode!=='PHYSICAL_READONLY'||data.task_dispatch_enabled)throw new Error('后台不是只读模式，请检查后台配置');
    if(!stopped){setState(data);setOnline(true);setError('');}
   }catch(e){if(!stopped){setOnline(false);setError(e.name==='AbortError'?'后台响应超时':e.message);}}
   finally{clearTimeout(timeout);if(!stopped)timer=setTimeout(poll,1000);}
  }
  poll();return()=>{stopped=true;clearTimeout(timer);controller?.abort();};
 },[]);
 const live=online&&state?.live,axisLive=live&&state?.axis_live;
 const signals=Object.fromEntries((state?.signals||[]).map(item=>[item.address,item.value]));
 const status=signals['DB2.DBW0'];
 const axes=['x','y','z'].map((axis,index)=>state?.axes?.find(item=>item.axis===axis)||{axis,name:['行走','升降','货叉'][index],address:`DB5.DBD${22+index*4}`,value:null});
 return <main className="physical-twin-page">
  <header className="physical-heading"><div className="physical-brand"><span className="brand-mark"><Box size={28}/></span><div><h1>立库真机展示</h1><p>三维装配 · PLC 实时反馈 · 局域网查看</p></div></div><span className="readonly-badge"><ShieldCheck size={18}/>只读模式</span></header>
  <div className={`physical-connection ${live?'is-live':'is-offline'}`} role="status"><span><Wifi size={18}/>{!state&&online?'正在连接后台…':live?'真机数据实时更新':'数据未更新 · 保留最后一次采样'}</span><span>{state?.endpoint||'等待 PLC 地址'} · 约 1 秒采样</span></div>
  {error&&<p className="physical-error" role="alert">{error}</p>}
  {state?.last_error&&<p className="physical-error">PLC 通信：{state.last_error}</p>}
  <section className="physical-stats" aria-label="PLC 状态">
   <div><span>CPU</span><strong>{live?state.cpu_state?.replace('S7CpuStatus',''):'—'}</strong><small>{state?.cpu_model||'等待连接'}</small></div>
   <div><span>设备状态{!live&&state?'（上次）':''}</span><strong>{statusLabels[status]??'—'}</strong><small>状态码 {status??'—'}</small></div>
   <div><span>任务 ID / 异常代码{!live&&state?'（上次）':''}</span><strong>{signals['DB2.DBW2']??'—'} / {signals['DB2.DBW4']??'—'}</strong><small>交互状态 {signals['DB2.DBW6']??'—'}</small></div>
   <div><span>最近采样</span><strong>{state?.updated_at?time(state.updated_at):'—'}</strong><small>采样计数 <b data-testid="sample-count">{state?.sample??0}</b> · {live?'有效':'待更新'}</small></div>
  </section>
  <div className="physical-workspace">
   <section className="panel physical-model"><div className="panel-head"><h2>三维真机位置</h2><span className="muted">{axisLive?'坐标已接入':'坐标待更新'}</span></div><p className="physical-note">{calibration?`${calibration.anchor_label}基准已固定，行走方向已反转，货叉 0 mm 对齐中间位置。其他位置与货叉联动待现场核对。`:'正在读取固定显示基准…'}</p><PanelBoundary name="三维装配">{calibration?<Suspense fallback={<div className="loading">正在加载三维引擎…</div>}><Twin physical state={state} calibration={calibration}/></Suspense>:<p className="physical-note" role="status">{calibrationError?`标定配置加载失败：${calibrationError}。请刷新重试。`:'正在加载显示标定配置…'}</p>}</PanelBoundary></section>
   <aside className={`panel physical-feedback ${axisLive?'':'feedback-stale'}`}><div className="panel-head"><h2><Activity size={18}/>轴坐标反馈</h2><span className="muted">{axisLive?'实时':'未更新'}</span></div><div className="physical-axes">{axes.map(item=><div key={item.axis}><span>{item.name}<small>{item.address}</small></span><strong data-testid={`axis-${item.axis}`}>{number(item.value)}<small>mm</small></strong></div>)}</div><p className="physical-note">{axisLive?'PLC 原始坐标值，每次采样刷新。':'坐标采样未就绪或已中断；模型保留最后位置。'}{state?.axis_error&&<span className="physical-error">{state.axis_error}</span>}</p><dl className="physical-details"><div><dt>接入设备</dt><dd>{state?.module_name||'—'}</dd></div><div><dt>坐标采样</dt><dd>{state?.axis_updated_at?time(state.axis_updated_at):'—'}</dd></div><div><dt>固定基准</dt><dd>{calibration?.anchor_label||'加载中'}</dd></div><div><dt>基准坐标 mm</dt><dd data-testid="fixed-anchor">{calibration?['x','y','z'].map(axis=>number(calibration.plc_anchor_mm[axis])).join(' / '):'—'}</dd></div><div><dt>行走映射</dt><dd>已反向 · 重启后基准保留</dd></div></dl><p className="physical-note">拖动旋转模型，双指缩放。图纸中的料箱是设计占位，页面不提供真实库存数量。</p><a className="physical-monitor-link" href="/monitor" target="_blank" rel="noreferrer">查看只读变量与采样记录 ↗</a></aside>
  </div>
  <footer><span>安卓平板与后台主机接入同一公司局域网</span><span>页面仅显示反馈 · 控制接口已禁用</span></footer>
 </main>;
}
