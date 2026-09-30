import React,{useEffect,useState,lazy,Suspense} from 'react';
import {createRoot} from 'react-dom/client';
import {Box,LayoutDashboard,Warehouse,ListChecks,FileBox,Plus,Info,Play,CheckCircle2,Package,Cpu,X,ExternalLink} from 'lucide-react';
import {api} from './api';
import {DevicePanel,TaskTable,Inventory,Structure,Events} from './Panels';
import PLCConsole,{PLCSummary,usePLCRuntime} from './PLCConsole';
import TaskForm from './TaskForm';
import PanelBoundary from './PanelBoundary';
import NativePLC from './NativePLC';
import './style.css';
const Twin=lazy(()=>import('./RealTwin'));
const PhysicalTwin=lazy(()=>import('./PhysicalTwin'));
const nav=[['overview','运行总览',LayoutDashboard],['plc','软 PLC 运行跟踪',Cpu],['native','原生 PLC 联调',Cpu],['inventory','库存与库位',Warehouse],['tasks','任务中心',ListChecks],['structure','结构资料',FileBox]];
const titles={native:'原生 PLC 联调',overview:'立库运行总览',plc:'PLC 运行跟踪',inventory:'库存与库位',tasks:'任务中心',structure:'结构资料'};

function App(){
 const [state,setState]=useState(null),[page,setPage]=useState('overview'),[modal,setModal]=useState(false),[error,setError]=useState(''),[online,setOnline]=useState(true),[selected,setSelected]=useState(null),[target,setTarget]=useState(null);
 const runtime=usePLCRuntime(page==='overview'||page==='plc');
 async function refresh(){const data=await api('/api/state');setState(data);setOnline(true);}
 useEffect(()=>{let stopped=false,timer,controller;async function poll(){controller=new AbortController();try{const data=await api('/api/state',undefined,{signal:controller.signal});if(!stopped){setState(data);setOnline(true);}}catch(e){if(!stopped&&e.name!=='AbortError')setOnline(false);}finally{if(!stopped)timer=setTimeout(poll,400);}}poll();return()=>{stopped=true;clearTimeout(timer);controller?.abort();};},[]);
 async function act(path,body){try{await api(path,body);setError('');await refresh();}catch(e){setError(e.message);}}
 function newTask(t=null){setTarget(t);setModal(true);}
 const loc=state?.locations[selected];
 return <div className="app-shell"><aside className="sidebar"><a href="#" className="brand" onClick={e=>{e.preventDefault();setPage('overview');}}><span className="brand-mark"><Box size={29}/></span><div><strong>立库调试套件</strong><small>WMS · WCS · SOFT PLC</small></div></a><nav aria-label="主导航">{nav.map(([id,label,Icon])=><button key={id} title={label} className={page===id?'active':''} onClick={()=>setPage(id)}><Icon size={19}/>{label}</button>)}</nav><div className="sidebar-bottom"><span className="sim-dot"/>本地软 PLC <small>MVP 0.2</small></div></aside><main><header><div><h1>{titles[page]}</h1><p>单巷道堆垛机 · 本机联调环境</p></div><button className="primary" onClick={()=>newTask()} disabled={!online||!state||page==='native'} title={page==='native'?'原程序任务握手尚未接入':undefined}><Plus size={17}/>新建任务</button></header><div className={`mode-banner ${online?'':'offline'}`} role="status"><span><Info size={17}/>{online?(page==='native'?'Siemens 原程序 · 本机 Advanced 仿真':'本机 S7 协议软 PLC · 未连接真实设备'):'服务连接中断 · 当前显示最后一次数据'}</span><span>{page==='native'?'官方 API 实时采集 · 无物理设备连接':'Python 扫描程序 · Odoo 未连接'}</span></div>{error&&<div className="error-banner" role="alert">{error}<button aria-label="关闭提示" onClick={()=>setError('')}><X size={17}/></button></div>}
 {!state?<section className="panel loading">{online?'正在读取仓库状态…':'无法连接服务，请检查 Python 服务是否运行。'}</section>:<>{page!=='plc'&&page!=='native'&&<section className="metrics">{[[Warehouse,'库位总数',Object.keys(state.locations).length],[Package,'在库载具',Object.values(state.loads).filter(l=>state.locations[l.location]).length],[Play,'执行中',state.tasks.filter(t=>['RUNNING','BLOCKED','RECOVERY_REQUIRED'].includes(t.status)).length],[CheckCircle2,'已完成',state.tasks.filter(t=>t.status==='COMPLETED').length]].map(([Icon,label,value])=><div key={label}><Icon size={27}/><div><span>{label}</span><b>{value}</b></div></div>)}</section>}
 <div className={!online?'stale':''} aria-disabled={!online}>{page==='native'?<PanelBoundary name="原生 PLC 联调"><NativePLC/></PanelBoundary>:page==='overview'?<><div className="workspace"><section className="panel mapping"><div className="panel-head"><h2><Warehouse size={19}/>仓库数字映射</h2><span className="muted">SC-01 · PLC 反馈驱动</span></div><PanelBoundary name="三维数字映射"><Suspense fallback={<div className="loading">正在加载三维引擎…</div>}><Twin state={state} onSelect={setSelected}/></Suspense></PanelBoundary></section><DevicePanel state={state} act={act}/></div><PanelBoundary name="PLC 实时摘要"><PLCSummary runtime={runtime} state={state} onOpen={()=>setPage('plc')}/></PanelBoundary><TaskTable state={state} act={act} onNew={()=>newTask()}/><Events events={state.events}/></>:page==='plc'?<PanelBoundary name="PLC 运行跟踪"><PLCConsole runtime={runtime} state={state}/></PanelBoundary>:page==='inventory'?<Inventory state={state} act={act} selected={selected} onSelect={setSelected}/>:page==='tasks'?<><TaskTable state={state} act={act} onNew={()=>newTask()} full/><div className="integration-note"><Info size={18}/><div><b>Odoo 桥接契约已预留</b><p>软 PLC 搬运完成后生成唯一回执。模拟确认用于验证去重，不代表真实库存已过账。</p></div><a href="/docs" target="_blank" rel="noreferrer">API 文档<ExternalLink size={14}/></a></div><Events events={state.events}/></>:<PanelBoundary name="结构资料"><Structure/></PanelBoundary>}</div>
 {modal&&<TaskForm state={state} initialTarget={target} onClose={()=>setModal(false)} onSubmit={async f=>{await api('/api/tasks',f);await refresh();}}/>}{loc&&<div className="selection-panel"><button aria-label="关闭库位详情" className="icon-button close-selection" onClick={()=>setSelected(null)}><X size={18}/></button><h3>{loc.id}</h3><p>{loc.side==='L'?'左':'右'}侧 · {loc.column}列 · {loc.level}层</p><div>载具：<b>{loc.load||'空闲'}</b></div><div>预留：{loc.reserved?state.tasks.find(t=>t.id===loc.reserved)?.number:'无'}</div>{!loc.load&&!loc.reserved&&<button className="primary" onClick={()=>{newTask(loc.id);setSelected(null);}}>入库到此位置</button>}</div>}</>}
 <footer><span>Python WCS · S7 TCP 软 PLC · 执行轨迹</span><span>图纸映射待标定 · 原程序运行状态见原生 PLC 联调</span></footer></main></div>;
}
const physicalReadonly=document.documentElement.dataset.appMode==='physical-readonly';
createRoot(document.getElementById('root')).render(physicalReadonly?<PanelBoundary name="真机只读展示"><Suspense fallback={<div className="loading">正在加载真机只读展示…</div>}><PhysicalTwin/></Suspense></PanelBoundary>:<App/>);

