import React,{useEffect,useState} from 'react';
import {api} from './api';

export default function NativePLC(){
 const [data,setData]=useState(null),[error,setError]=useState(''),[query,setQuery]=useState('');
 useEffect(()=>{let stopped=false,timer,controller;async function poll(){controller=new AbortController();try{const value=await api('/api/native-plc',undefined,{signal:controller.signal});if(!stopped){setData(value);setError('');}}catch(e){if(!stopped&&e.name!=='AbortError')setError(e.message);}finally{if(!stopped)timer=setTimeout(poll,750);}}poll();return()=>{stopped=true;clearTimeout(timer);controller?.abort();};},[]);
 const live=data?.live&&!error,rows=(data?.signals||[]).filter(row=>row.name.includes(query));
 return <section className="panel" style={{padding:24}}><div className="panel-head"><h2>Siemens 原程序 · 实时监视</h2><b style={{color:live?'#087f5b':'#b45309'}}>{live?data.state:'采集离线 / 数据过期'}</b></div>
 <p>原工程 PLC_2 → PLCSIM Advanced → 官方 API → 本机界面</p>
 <div className="integration-note">此面板读取原 PLC 程序。现有任务中心仍使用 Python 软 PLC；原程序任务握手、驱动及传感器反馈仿真正在接入。</div>
 {error&&<p role="alert">{error}</p>}
 <dl><dt>实例</dt><dd>{data?.instance||'—'}</dd><dt>采样序号</dt><dd>{data?.sample??'—'}</dd><dt>采集时间</dt><dd>{data?.updated_at?new Date(data.updated_at).toLocaleString('zh-CN'):'—'}</dd><dt>许可</dt><dd>{data?.license||'—'}</dd></dl>
 <details><summary>查看 PLC 调用验证</summary><pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify(data?.read_write_probe||{},null,2)}</pre><p>同值写回用于验证传输，不代表搬运任务或运动闭环已通过。</p></details>
 <label style={{display:'block',margin:'20px 0'}}>筛选 PLC 符号 <input aria-label="筛选 PLC 符号" value={query} onChange={e=>setQuery(e.target.value)} placeholder="状态 / 任务 / 输入名称"/></label>
 <p>{rows.length} 个信号 · {live?'实时数据':'最后采样，仅供查看'}</p>
 <div style={{overflowX:'auto',maxHeight:560,overflowY:'auto'}}><table style={{width:'100%'}}><thead><tr><th style={{textAlign:'left'}}>PLC 符号</th><th>当前值</th><th>读取结果</th></tr></thead><tbody>{rows.map(row=><tr key={row.name}><td>{row.name}</td><td style={{textAlign:'center'}}>{row.value===null?'—':String(row.value)}</td><td>{row.error||'OK'}</td></tr>)}</tbody></table></div>
 </section>;
}
