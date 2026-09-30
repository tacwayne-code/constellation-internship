import React,{useCallback,useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Box,Download,Upload,ClipboardList,MapPin,Settings,LockKeyhole,UnlockKeyhole,X} from 'lucide-react';
import {api,getKey,saveKey} from './api';
import {Unlock} from './components';
import OperationPage from './OperationPage';
import TaskPage from './TaskPage';
import {InventoryPage} from './WarehousePages';
import LocationPage from './LocationPage';
import DevicePage from './DevicePage';
import './wms.css';

const nav=[['inbound','入库',Download],['outbound','出库',Upload],['inventory','库存',Box],['tasks','任务',ClipboardList],['locations','库位',MapPin],['device','设备',Settings]];
const titles={inbound:['物料入库','创建单据、取箱装料、返库自动记账'],outbound:['物料出库','按单取箱、扫码取料、余料或空箱返库'],inventory:['库存管理','查询在库料箱，登记现场已有库存'],tasks:['作业任务','核对下发、跟踪执行，正常完成后自动确认回执'],locations:['库位管理','按实际货架建档，核对空位后参与分配'],device:['设备状态','真实 PLC 连接、接单条件与操作记录']};
function App(){
 const [page,setPage]=useState('inbound'),[state,setState]=useState(null),[online,setOnline]=useState(true),[unlocked,setUnlocked]=useState(false),[unlock,setUnlock]=useState(false),[notice,setNotice]=useState(null);
 const notify=useCallback((message,type='success')=>setNotice({message,type}),[]);
 const refresh=useCallback(async()=>{const value=await api('/api/state');setState(value);setOnline(true);return value;},[]);
 useEffect(()=>{
  let stopped=false,timer,controller;
  async function poll(){controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),5000);try{const value=await api('/api/state',undefined,{signal:controller.signal});if(!stopped){setState(value);setOnline(true);}}catch{if(!stopped)setOnline(false);}finally{clearTimeout(timeout);if(!stopped)timer=setTimeout(poll,1000);}}
  poll();return()=>{stopped=true;clearTimeout(timer);controller?.abort();};
 },[]);
 useEffect(()=>{let active=true;if(getKey())api('/api/auth/check',{}).then(()=>{if(active)setUnlocked(true);}).catch(()=>{});function locked(){setUnlocked(false);}window.addEventListener('wms-locked',locked);return()=>{active=false;window.removeEventListener('wms-locked',locked);};},[]);
 const canEdit=online&&unlocked;
 const props={state,canEdit,refresh,notify};
 return <div className="wm-shell"><aside className="wm-sidebar"><a href="#" className="wm-brand" onClick={event=>{event.preventDefault();setPage('inbound');}}><Box size={28}/><strong>仓储作业台</strong></a><nav aria-label="仓储导航">{nav.map(([id,label,Icon])=><button key={id} className={page===id?'active':''} onClick={()=>{setPage(id);setNotice(null);}}><Icon size={25}/><span>{label}</span></button>)}</nav><p className="wm-sidebar-footer">WMS · 现场作业</p></aside><main className="wm-main"><header className="wm-header"><div><h1>{titles[page][0]}</h1><p>{titles[page][1]}</p></div><div className="wm-header-controls"><span className="wm-connection"><i className={online&&state?.plc.live?'online':'offline'}/>{online&&state?.plc.live?'PLC 在线':'连接待就绪'} · {unlocked?'操作已解锁':'控制已锁定'}</span><button className="wm-unlock" onClick={()=>{if(unlocked){saveKey('');setUnlocked(false);setNotice(null);}else setUnlock(true);}}>{unlocked?<UnlockKeyhole size={21}/>:<LockKeyhole size={21}/>}<span>{unlocked?'锁定操作':'解锁操作'}</span></button></div></header>
 {!online&&<div className="wm-alert" role="alert">后台连接中断，当前显示最后一次数据，操作暂不可用。</div>}
 {notice&&<div className={`wm-toast ${notice.type==='error'?'wm-alert':''}`} role={notice.type==='error'?'alert':'status'}><span>{notice.message}</span><button className="wm-icon" aria-label="关闭提示" onClick={()=>setNotice(null)}><X size={19}/></button></div>}
 {!state?<section className="wm-panel wm-loading">{online?'正在读取仓储状态…':'无法连接后台，请检查服务和局域网。'}</section>:page==='inbound'||page==='outbound'?<OperationPage key={page} kind={page==='inbound'?'INBOUND':'OUTBOUND'} {...props} onPage={setPage}/>:page==='inventory'?<InventoryPage {...props}/>:page==='locations'?<LocationPage {...props}/>:page==='tasks'?<TaskPage {...props}/>:<DevicePage {...props}/>}
 <footer className="wm-footer"><span>本地部署 · 数据保存在后台主机</span><span>{state?.write_enabled?'真机读写已启用 · 核对后下发':'正在读取服务状态'}</span></footer></main>{unlock&&<Unlock onClose={()=>setUnlock(false)} onUnlocked={()=>{setUnlocked(true);notify('操作已解锁');}}/>}</div>;
}
createRoot(document.getElementById('root')).render(<App/>);
