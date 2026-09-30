import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Activity,ArrowDownLeft,ArrowUpRight,ChevronRight,Code2,Cpu,Pause,Play,Radio,RefreshCw,StepForward,TriangleAlert,Unplug,Wifi} from 'lucide-react';
import {api,phases} from './api';

const SOURCE_FILES=['plc/runtime.py','backend/plc_controller.py','plc/protocol.py'];
const SOURCE_LABELS={'plc/runtime.py':'PLC 扫描程序','backend/plc_controller.py':'WCS 调用程序','plc/protocol.py':'DB 协议定义'};
const EMPTY=[];
const format=(value)=>value==null?'—':typeof value==='object'?JSON.stringify(value):String(value);
const stamp=(value)=>value?new Date(value).toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit',fractionalSecondDigits:3}):'—';
const phaseLabel=(value)=>phases[value]||value||'等待任务';
const recent=(trace,predicate)=>{for(let i=trace.length-1;i>=0;i--)if(predicate(trace[i]))return trace[i];return null;};
function sourceProof(event,source,error){
 if(!event)return {valid:false,message:'尚无实际执行轨迹。'};
 if(!event.source_sha256)return {valid:false,message:'历史记录未附版本，无法验证源码行号；已关闭高亮。'};
 if(error)return {valid:false,message:`源码读取失败：${error}；已关闭高亮。`};
 if(!source)return {valid:false,message:'正在读取对应源代码…'};
 if(!source.sha256)return {valid:false,message:'当前源码未附 SHA-256 版本，无法验证行号；已关闭高亮。'};
 if(event.source_sha256!==source.sha256)return {valid:false,message:'运行源码与磁盘版本不同，请重启对应进程；已关闭高亮。'};
 if(!source.lines?.some(line=>line.number===event.line))return {valid:false,message:'轨迹行号不在当前源码中，已关闭高亮。'};
 return {valid:true,message:''};
}


export function usePLCRuntime(enabled=true){
 const [data,setData]=useState(null),[error,setError]=useState(''),[sources,setSources]=useState({}),[sourceErrors,setSourceErrors]=useState({});
 const loadSources=useCallback(async(signal)=>{
  await Promise.allSettled(SOURCE_FILES.map(async file=>{
   try {
    const source=await api(`/api/plc/source?file=${encodeURIComponent(file)}`,undefined,{signal});
    if(signal?.aborted)return;
    setSources(previous=>({...previous,[file]:source}));
    setSourceErrors(previous=>{const next={...previous};delete next[file];return next;});
   } catch(e) {if(e.name!=='AbortError'&&!signal?.aborted)setSourceErrors(previous=>({...previous,[file]:e.message}));}
  }));
 },[]);
 const refresh=useCallback(async()=>{
  const sourceRequest=loadSources();
  try {const next=await api('/api/plc/diagnostics');setData(next);setError('');return next;}
  catch(e){setError(e.message);return null;}
  finally{await sourceRequest;}
 },[loadSources]);
 useEffect(()=>{if(!enabled)return;let stopped=false,timer,controller;async function poll(){controller=new AbortController();try{const next=await api('/api/plc/diagnostics',undefined,{signal:controller.signal});if(!stopped){setData(next);setError('');}}catch(e){if(!stopped&&e.name!=='AbortError')setError(e.message);}finally{if(!stopped)timer=setTimeout(poll,700);}}poll();return()=>{stopped=true;clearTimeout(timer);controller?.abort();};},[enabled]);
 useEffect(()=>{if(!enabled)return;let stopped=false,timer;const controller=new AbortController();async function pollSources(){await loadSources(controller.signal);if(!stopped)timer=setTimeout(pollSources,5000);}pollSources();return()=>{stopped=true;clearTimeout(timer);controller.abort();};},[enabled,loadSources]);
 return {data,error,sources,sourceErrors,refresh};
}

function SourcePreview({title,event,sources,sourceErrors,idle}){
 const source=event?sources[event.file]:null;
 const proof=sourceProof(event,source,event?sourceErrors[event.file]:null);
 const rows=proof.valid?source.lines.filter(row=>Math.abs(row.number-event.line)<=2):EMPTY;
 return <div className="plc-preview"><div className="plc-preview-head"><span><Code2 size={14}/>{title}</span><small>{event?.scan!=null?`扫描 ${event.scan}`:'尚无执行点'}</small></div>{event?<><div className="plc-file-label" title={proof.valid?`源码 SHA-256 已核对：${source.sha256}`:proof.message}>{event.file}:{event.line} <span>{event.function?`${event.function}()` : event.event}</span></div>{proof.valid?<div className="plc-mini-code">{rows.map(row=><div key={row.number} className={row.number===event.line?'executed':''}><span>{row.number}</span><code>{row.text||' '}</code></div>)}</div>:<p className="plc-empty-line" role="status">{proof.message}</p>}<p className="plc-preview-caption">{idle?'最近执行点 · 当前无搬运任务':event.message||event.event} <time>{stamp(event.time)}</time></p></>:<p className="plc-empty-line">等待真实执行轨迹；尚无代码行可高亮。</p>}</div>;
}

export function PLCSummary({runtime,state,onOpen}){
 const {data,error,sources,sourceErrors}=runtime,plc=data?.plc||{},trace=data?.trace||EMPTY;
 const connected=!!data?.connected&&!error,idle=!plc.active_task;
 const wcs=recent(trace,e=>e.process==='WCS'&&e.file&&e.line),scan=recent(trace,e=>e.process==='PLC'&&e.file&&e.line);
 return <section className="panel plc-summary"><div className="panel-head"><h2><Cpu size={18}/>PLC 实时执行</h2><button className="text-button" onClick={onOpen}>打开运行跟踪<ChevronRight size={14}/></button></div><div className="plc-summary-body"><div className="plc-link-state"><span className={`status ${connected?'good':error||data?'bad':'waiting'}`}><i/>{connected?'S7 TCP 已连接':error?'诊断读取失败':data?'S7 连接中断':'读取中'}</span><strong>{data?.endpoint||'本机软 PLC'}</strong><span>Python 扫描程序 · {data?.cpu_state||plc.cpu_state||'状态待回读'}</span><div className="plc-mini-metrics"><div><b>{format(plc.scan)}</b><small>PLC 扫描号</small></div><div><b>{format(data?.tx_count)} / {format(data?.rx_count)}</b><small>实际 TX / RX</small></div></div><p>{error||data?.last_error||(idle?'PLC 扫描运行，等待搬运任务。':`反馈阶段：${phaseLabel(plc.phase)}`)}</p></div><SourcePreview title="WCS → PLC 调用" event={wcs} sources={sources} sourceErrors={sourceErrors} idle={idle}/><SourcePreview title="PLC → 设备反馈" event={scan} sources={sources} sourceErrors={sourceErrors} idle={idle}/></div><div className="plc-scope-note">独立本机 S7 协议软 PLC · 源码版本核对一致后才显示执行行 · 未执行原厂 .ap18 工程</div></section>;
}

function SourcePanel({source,sourceError,event,follow,selectedFile,onSelect,onFollow}){
 const scroller=useRef(null),active=useRef(null);
 const proof=sourceProof(event,source,sourceError),activeLine=proof.valid?event.line:null;
 useEffect(()=>{if(follow&&activeLine!=null&&scroller.current&&active.current){const box=scroller.current,node=active.current;box.scrollTop=Math.max(0,node.offsetTop-box.clientHeight/2+node.clientHeight/2);}},[activeLine,selectedFile,follow,source]);
 return <section className="panel plc-source"><div className="panel-head"><h2><Code2 size={18}/>实时源代码</h2><label className="plc-follow"><input type="checkbox" checked={follow} onChange={e=>onFollow(e.target.checked)}/>跟随执行行</label></div><div className="plc-source-tabs" role="tablist" aria-label="运行源码文件">{SOURCE_FILES.map(file=><button role="tab" aria-selected={selectedFile===file} className={selectedFile===file?'active':''} key={file} onClick={()=>onSelect(file)}>{SOURCE_LABELS[file]}</button>)}</div><div className="plc-source-path"><code title={source?.sha256?`磁盘源码 SHA-256：${source.sha256}`:undefined}>{selectedFile}</code><span>{proof.valid?`版本已核对 · L${event.line} · ${stamp(event.time)}`:event?`轨迹 L${event.line} · 高亮已关闭`:'尚无实际执行轨迹'}</span></div>{event&&!proof.valid&&<div className="error-banner" role="status">{proof.message}</div>}<div className="plc-code-viewport" ref={scroller} tabIndex={0} aria-label={`${selectedFile} 源代码`}>{sourceError?<p className="plc-code-message">源代码读取失败：{sourceError}</p>:source?.lines?<div className="plc-code-lines">{source.lines.map(line=><div key={line.number} className={`plc-code-line ${line.number===activeLine?'executed':''}`} ref={line.number===activeLine?active:null}><span className="plc-code-number">{line.number===activeLine?'▶ ':''}{line.number}</span><code>{line.text||' '}</code></div>)}</div>:<p className="plc-code-message">正在读取服务端源代码…</p>}</div><div className="plc-source-event"><Activity size={15}/><span>{event?`${event.function||event.event} · ${event.message||event.event}`:'收到已核对源码版本的执行轨迹后才会标记对应行。'}</span></div></section>;
}

function RegisterTable({registers,stale}){
 return <section className="panel plc-registers"><div className="panel-head"><h2>DB2 寄存器</h2><span className="muted">{stale?'最后回读快照':'S7 实际回读'}</span></div><div className="table-scroll"><table><thead><tr><th>地址</th><th>含义</th><th>值</th><th>类型</th></tr></thead><tbody>{registers.map(register=><tr key={register.address||`${register.db}-${register.offset}`}><td><code>{register.address||`DB${register.db}.DBW${register.offset}`}</code></td><td>{register.label}</td><td className="plc-register-value">{format(register.value)}</td><td>{register.type||'INT'}</td></tr>)}</tbody></table>{!registers.length&&<p className="plc-empty-line">尚未收到 DB2 回读，未填充默认值。</p>}</div><p className="plc-register-note">DB2 对应项目交互表：DBW0 的 8=空闲、9=忙、7=异常，1/2/3=出库/入库/调仓完成；源与目标字段顺序为排/层/列/伸位。DBW6 的接单握手与 DB100/101 为本地 SIM 扩展，不能直接作为现场协议。</p></section>;
}

export default function PLCConsole({runtime,state}){
 const {data,error,sources,sourceErrors,refresh}=runtime;
 const [selectedFile,setSelectedFile]=useState('plc/runtime.py'),[follow,setFollow]=useState(true),[filter,setFilter]=useState('ALL'),[expanded,setExpanded]=useState(null),[busy,setBusy]=useState(false),[controlError,setControlError]=useState('');
 const plc=data?.plc||{},trace=data?.trace||EMPTY,connected=!!data?.connected&&!error,registers=data?.last_feedback?(data?.registers||EMPTY):EMPTY,paused=!!plc.paused||!!data?.recovery_hold,recovery=!!plc.recovery||!!data?.recovery_hold;
 const selectedEvent=recent(trace,e=>e.file===selectedFile&&e.line);
 const events=trace.filter(event=>filter==='ALL'||event.process===filter||event.direction===filter).slice(-80).reverse();
 async function control(action,value){if(busy)return;setBusy(true);setControlError('');try{await api('/api/plc/control',{action,...(value===undefined?{}:{value})});await refresh();}catch(e){setControlError(e.message);}finally{setBusy(false);}}
 return <div className="plc-console"><div className="plc-intro"><div><h2>从 WCS 请求到 PLC 扫描，逐步看清每次执行</h2><p>运行的是独立进程的 Python S7 协议软 PLC。原厂 Siemens PLC 工程未被执行，真实设备未连接。</p></div><a href="/docs" target="_blank" rel="noreferrer">API 文档<ChevronRight size={14}/></a></div>{(error||controlError)&&<div className="error-banner" role="alert">{controlError||`诊断接口不可用：${error}。下方保留最后读取值。`}</div>}<section className="plc-kpis">{[[Radio,'S7 TCP 连接',connected?'已连接':data?'已断开':'读取中',data?.endpoint||'等待端点信息'],[Cpu,'CPU / 进程',data?.cpu_state||plc.cpu_state||'—',plc.pid?`PID ${plc.pid} · ${plc.boot||'启动信息未回读'}`:'等待软 PLC 反馈'],[Activity,'PLC 扫描号',format(plc.scan),data?.cycle_ms!=null?`配置周期 ${data.cycle_ms} ms`:'扫描号来自 PLC 回读'],[ArrowUpRight,'通信计数',`${format(data?.tx_count)} / ${format(data?.rx_count)}`,'WCS 发送 / 接收 · TX / RX']].map(([Icon,label,value,detail])=><div className="panel" key={label}><span><Icon size={16}/>{label}</span><b>{value}</b><small title={detail}>{detail}</small></div>)}</section><section className="panel plc-toolbar"><div><span className={`status ${connected&&!plc.fault?'good':'bad'}`}><i/>{!connected?'通信中断':plc.fault?'故障':paused?'执行已暂停':plc.active_task?'任务执行中':'扫描中 · 无搬运任务'}</span><span className="plc-current-phase">{phaseLabel(plc.phase)} {plc.active_task?`· 任务 ${plc.active_task}`:''}</span></div><div className="plc-actions"><button disabled={busy||!connected||!!plc.fault} className="primary compact" onClick={()=>control(paused?'resume':'pause')}>{paused?<Play size={15}/>:<Pause size={15}/>} {paused?'恢复执行':'暂停执行'}</button><button className="compact" disabled={busy||!connected||!paused||!!plc.fault||recovery} onClick={()=>control('step')}><StepForward size={15}/>单步执行</button><button className="compact" disabled={busy||!connected} onClick={()=>control(plc.fault?'clear_fault':'fault')}><TriangleAlert size={15}/>{plc.fault?'清除模拟故障':'注入模拟故障'}</button><button className="compact" disabled={busy||!!error||!data} onClick={()=>control(connected?'disconnect':'reconnect')}>{connected?<Unplug size={15}/>:<Wifi size={15}/>} {connected?'断开 S7 通信':'重连 S7 通信'}</button><button className="compact" disabled={busy} onClick={refresh} aria-label="刷新 PLC 诊断"><RefreshCw size={15}/></button></div>{data?.last_error&&<p className="warning-text">{data.last_error}</p>}{recovery&&<p className="warning-text">恢复点已保留。核对载具位置后，使用“恢复执行”继续本地仿真。</p>}</section><div className="plc-inspection-grid"><SourcePanel source={sources[selectedFile]} sourceError={sourceErrors[selectedFile]} event={selectedEvent} follow={follow} selectedFile={selectedFile} onSelect={setSelectedFile} onFollow={setFollow}/><RegisterTable registers={registers} stale={!connected}/></div><section className="panel plc-trace"><div className="panel-head"><h2>调用与扫描流水 <small>{events.length} 条可见</small></h2><select aria-label="筛选 PLC 流水" value={filter} onChange={e=>setFilter(e.target.value)}><option value="ALL">全部事件</option><option value="WCS">WCS 调用</option><option value="PLC">PLC 扫描</option><option value="TX">仅 TX 发送</option><option value="RX">仅 RX 接收</option></select></div><div className="table-scroll"><table><thead><tr><th>时间</th><th>进程 / 方向</th><th>扫描号</th><th>事件 / 地址</th><th>调用代码</th><th>说明</th></tr></thead><tbody>{events.map(event=><ReactTrace key={event.id} event={event} expanded={expanded===event.id} onExpand={()=>setExpanded(expanded===event.id?null:event.id)} onSource={()=>{if(SOURCE_FILES.includes(event.file))setSelectedFile(event.file);}}/>)}</tbody></table>{!events.length&&<p className="plc-empty-line">尚无符合条件的真实执行记录。</p>}</div><p className="plc-trace-note">TX / RX 显示实际 S7 调用记录；点击记录查看传输区数据。仅在源码 SHA-256 匹配时保留最后命中行，不能据此判断当前指令仍在执行。</p></section></div>;
}

function ReactTrace({event,expanded,onExpand,onSource}){
 return <><tr><td className="plc-time">{stamp(event.time)}</td><td><span className={`plc-direction ${event.direction||event.process}`}>{event.direction==='TX'?<ArrowUpRight size={12}/>:event.direction==='RX'?<ArrowDownLeft size={12}/>:<Activity size={12}/>} {event.process} {event.direction||''}</span></td><td>{format(event.scan)}</td><td><button className="text-button" onClick={onExpand}>{event.event}{event.db!=null?` · DB${event.db}${event.offset!=null?`.${event.offset}`:''}`:''}</button></td><td>{event.file?<button className="text-button plc-trace-source" onClick={onSource}>{event.file}:{event.line}</button>:'—'}</td><td className="plc-trace-message">{event.message||'—'}</td></tr>{expanded&&<tr><td colSpan="6"><div className="plc-packet"><p><b>函数</b> {event.function||'—'}　<b>记录 ID</b> {event.id}</p><p><b>传输区 HEX</b> <code>{event.hex||'此事件不含报文数据'}</code></p><pre>{event.decoded==null?'无附加解析数据':typeof event.decoded==='string'?event.decoded:JSON.stringify(event.decoded,null,2)}</pre></div></td></tr>}</>;
}

