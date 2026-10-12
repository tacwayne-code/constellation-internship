import React,{useCallback,useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Box,Download,Upload,ClipboardList,MapPin,Settings,Settings2,LogOut,UserRound,X} from 'lucide-react';
import {api,clearSession,setSession,hasPermission,setDeviceScope} from './api';
import OperationPage from './OperationPage';
import TaskPage from './TaskPage';
import {InventoryPage} from './WarehousePages';
import LocationPage from './LocationPage';
import DevicePage from './DevicePage';
import LoginPage from './LoginPage';
import SettingsPage from './SettingsPage';
import {Empty} from './components';
import './wms.css';

const nav=[['inbound','入库',Download],['outbound','出库',Upload],['inventory','库存',Box],['tasks','任务',ClipboardList],['locations','库位',MapPin],['device','设备',Settings],['settings','设置',Settings2]];
const titles={inbound:['物料入库','创建单据、取箱装料、返库自动记账'],outbound:['物料出库','按单取箱、扫码取料、余料或空箱返库'],inventory:['库存管理','查询在库料箱，登记现场已有库存'],tasks:['作业任务','核对下发、跟踪执行，正常完成后自动确认回执'],locations:['库位管理','按实际货架建档，核对空位后参与分配'],device:['设备状态','真实 PLC 连接、接单条件与操作记录'],settings:['系统设置','管理仓库、设备、用户及操作权限']};
function savedContext(){try{const value=JSON.parse(sessionStorage.getItem('wms-context-v1'));if(value&&/^(main|[0-9a-f]{32})$/.test(value.warehouse)&&/^(primary|[0-9a-f]{32}|)$/.test(value.device))return value;}catch{}return {warehouse:'main',device:'primary'};}
function App(){
 const [context,setContext]=useState(savedContext),[sites,setSites]=useState(null);
 const contextRef=useRef(context);
 const selectContext=useCallback(next=>{contextRef.current=next;setDeviceScope(next.device);sessionStorage.setItem('wms-context-v1',JSON.stringify(next));setContext(next);setState(null);setNotice(null);setOnline(true);},[]);
 useEffect(()=>{setDeviceScope(context.device);},[context.device]);
 const loadSites=useCallback(async()=>{const value=await api('/api/sites');setSites(value);const current=contextRef.current;const warehouse=value.warehouses.find(w=>w.id===current.warehouse)||value.warehouses[0];const devices=value.devices.filter(d=>d.warehouse_id===warehouse?.id);const next={warehouse:warehouse?.id||'',device:devices.find(d=>d.id===current.device)?.id||devices[0]?.id||''};if(next.warehouse!==current.warehouse||next.device!==current.device)selectContext(next);return value;},[selectContext]);
 const [page,setPage]=useState('inbound'),[state,setState]=useState(null),[online,setOnline]=useState(true),[session,setAuth]=useState(undefined),[authInfo,setAuthInfo]=useState(null),[loginNotice,setLoginNotice]=useState(''),[notice,setNotice]=useState(null),[logoutBusy,setLogoutBusy]=useState(false);
 const notify=useCallback((message,type='success')=>setNotice({message,type}),[]);
 const reset=useCallback((message='')=>{clearSession();setAuth(null);setState(null);setSites(null);setPage('inbound');setNotice(null);setLoginNotice(message);},[]);
 const initialize=useCallback(async()=>{setLoginNotice('');try{const [info,current]=await Promise.all([api('/api/auth/status'),api('/api/auth/me',undefined,{silentAuth:true}).catch(e=>{if(e.status===401)return null;throw e;})]);setAuthInfo(info);setSession(current);setAuth(current);}catch(e){setAuth(null);setLoginNotice(e.message);}},[]);
 useEffect(()=>{initialize();},[initialize]);
 useEffect(()=>{const loggedOut=()=>reset('登录已失效，请重新登录');window.addEventListener('wms-logged-out',loggedOut);return()=>window.removeEventListener('wms-logged-out',loggedOut);},[reset]);
 const refresh=useCallback(async()=>{const value=await api('/api/state');if(value.scope.device_id!==contextRef.current.device)throw new Error('设备已切换，请在当前设备重新操作');setState(value);setOnline(true);setAuth(old=>old?{...old,user:value.user}:old);return value;},[]);
 const userId=session?.user.id;
 useEffect(()=>{if(!userId)return;let stopped=false;const load=()=>{if(!stopped)loadSites().catch(e=>{if(e.status!==401&&!stopped)notify(e.message,'error');});};load();const timer=setInterval(load,10000);return()=>{stopped=true;clearInterval(timer);};},[userId,loadSites,notify]);
 useEffect(()=>{
  if(!userId||!context.device||!sites)return;
  let stopped=false,timer,controller;
  async function poll(){controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),5000);try{const value=await api('/api/state',undefined,{signal:controller.signal});if(!stopped&&value.scope.device_id===contextRef.current.device){setState(value);setOnline(true);setAuth(old=>old?{...old,user:value.user}:old);}}catch(e){if(!stopped&&e.status!==401)setOnline(false);}finally{clearTimeout(timeout);if(!stopped)timer=setTimeout(poll,1000);}}
  poll();return()=>{stopped=true;clearTimeout(timer);controller?.abort();};
 },[userId,context.device,Boolean(sites)]);
 async function logout(){if(logoutBusy)return;setLogoutBusy(true);try{await api('/api/auth/logout',{});reset();}catch(e){if(e.status!==401)notify(e.message,'error');}finally{setLogoutBusy(false);}}
 function loggedIn(value){setSession(value);setAuth(value);setAuthInfo(old=>({...old,initialized:true}));setLoginNotice('');setOnline(true);}
 if(!session)return <LoginPage info={authInfo} notice={loginNotice} onRetry={initialize} onLogin={loggedIn}/>;
 const user=session.user;
 const availableDevices=(sites?.devices||[]).filter(d=>d.warehouse_id===context.warehouse);
 function chooseWarehouse(id){const device=sites.devices.find(d=>d.warehouse_id===id);selectContext({warehouse:id,device:device?.id||''});}
 function chooseDevice(id){selectContext({warehouse:context.warehouse,device:id});}
 const permission=page==='device'?'device':page;
 const canEdit=online&&(!['inbound','outbound'].includes(page)||state?.write_enabled===true)&&(page==='tasks'?user.permissions.some(p=>['inbound','outbound'].includes(p)):page==='device'?user.permissions.some(p=>['device','control'].includes(p)):hasPermission({user},permission));
 const props={state,canEdit,refresh,notify};
 return <div className="wm-shell"><aside className="wm-sidebar"><a href="#" className="wm-brand" onClick={event=>{event.preventDefault();setPage('inbound');}}><Box size={28}/><strong>仓储作业台</strong></a><nav aria-label="仓储导航">{nav.map(([id,label,Icon])=><button key={id} aria-current={page===id?'page':undefined} className={page===id?'active':''} onClick={()=>{setPage(id);setNotice(null);}}><Icon size={23}/><span>{label}</span></button>)}</nav><p className="wm-sidebar-footer">WMS · 现场作业</p></aside><main className="wm-main"><header className="wm-header"><div><h1>{titles[page][0]}</h1><p>{titles[page][1]}</p></div><div className="wm-header-controls"><span className="wm-connection"><i className={online&&state?.plc.live?'online':'offline'}/>{online&&state?.plc.live?'PLC 在线':'连接待就绪'}</span><button className="wm-user-button" onClick={()=>setPage('settings')}><UserRound size={18}/><span>{user.display_name}</span></button><button className="wm-logout" aria-label="退出登录" onClick={logout} disabled={logoutBusy}><LogOut size={18}/><span>退出登录</span></button></div></header>
 <section className="wm-context-bar" aria-label="当前仓库和设备"><label>仓库<select aria-label="当前仓库" value={context.warehouse} disabled={!sites} onChange={e=>chooseWarehouse(e.target.value)}>{sites?.warehouses.map(w=><option key={w.id} value={w.id}>{w.name}</option>)}</select></label><label>设备<select aria-label="当前设备" value={context.device} disabled={!availableDevices.length} onChange={e=>chooseDevice(e.target.value)}>{availableDevices.length?availableDevices.map(d=><option key={d.id} value={d.id}>{d.name}{d.enabled?'':' · 连接已停用'}</option>):<option value="">尚未添加设备</option>}</select></label><span>库位、库存与任务对应所选设备</span></section>
 {!online&&<div className="wm-alert" role="alert">后台连接中断，当前显示最后一次数据，操作暂不可用。</div>}
 {notice&&<div className={`wm-toast ${notice.type==='error'?'wm-alert':''}`} role={notice.type==='error'?'alert':'status'}><span>{notice.message}</span><button className="wm-icon" aria-label="关闭提示" onClick={()=>setNotice(null)}><X size={19}/></button></div>}
 <div className={`wm-page wm-page-${page}`} key={page==='settings'?`${page}:${user.id}`:`${page}:${user.id}:${context.warehouse}:${context.device}`}>{page==='settings'?<SettingsPage key={user.manage_users?'manage':'personal'} user={user} notify={notify} sites={sites} warehouseId={context.warehouse} onSitesChanged={loadSites} onPasswordChanged={()=>reset('密码已更新，请使用新密码登录')}/>:!context.device?<section className="wm-panel wm-loading"><Empty title="该仓库尚未添加设备">{user.manage_users?<button onClick={()=>setPage('settings')}>进入设置添加设备</button>:<p>请联系管理员添加设备。</p>}</Empty></section>:!state?<section className="wm-panel wm-loading">{online?'正在读取仓储状态…':'无法连接后台，请检查服务和局域网。'}</section>:page==='inbound'||page==='outbound'?<OperationPage kind={page==='inbound'?'INBOUND':'OUTBOUND'} {...props} onPage={setPage}/>:page==='inventory'?<InventoryPage {...props}/>:page==='locations'?<LocationPage {...props}/>:page==='tasks'?<TaskPage {...props}/>:<DevicePage {...props}/>}</div>
 <footer className="wm-footer"><span>本地部署 · 数据保存在后台主机</span><span>{user.role==='admin'?'管理员 · 全部权限':user.permissions.length?'按用户权限开放操作':'只读用户 · 查询与查看'}</span></footer></main></div>;
}
createRoot(document.getElementById('root')).render(<App/>);
