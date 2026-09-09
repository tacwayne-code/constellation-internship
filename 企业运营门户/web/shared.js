(() => {
  if(parent!==window)document.body.classList.add('embedded');
  const state = {payload:null, inFlight:null, received:0, key:''};
  const bar=document.createElement('div');bar.className='lighthouse-status';bar.dataset.status='loading';
  const label=document.createElement('span');label.textContent='正在读取看板快照';
  const action=document.createElement('button');action.textContent='刷新';
  bar.append(label,action);
  const empty=document.createElement('div');empty.className='lighthouse-empty';
  const icon=document.createElement('div');icon.className='beacon';icon.textContent='◈';
  const title=document.createElement('strong');title.textContent='正在连接灯塔';
  const detail=document.createElement('p');detail.textContent='等待第一份有效数据';empty.append(icon,title,detail);
  document.body.append(bar,empty);
  const labels={loading:'等待首次采集',not_configured:'尚未接入 Odoo',live:'数据已同步',partial:'部分数据不可用',stale:'数据已过期',error:'连接失败',unauthorized:'需要展示访问口令'};
  function paint(payload){
    state.payload=payload;state.received=Date.now();
    const status=payload.status || 'error';
    bar.dataset.status=status;document.body.dataset.dataState=status;
    empty.hidden=Boolean(payload.ok && payload.data);
    const timestamp=payload.updatedAt?new Date(payload.updatedAt).toLocaleString('zh-CN',{hour12:false}):'尚无有效快照';
    label.textContent=`${labels[status]||status} · ${timestamp}${payload.data?.meta?.range?' · '+payload.data.meta.range:''}${status==='partial'?' · '+payload.issues.length+' 项读取不完整':''}`;
    title.textContent=labels[status]||'暂时无法显示';
    detail.textContent=status==='not_configured'?'配置 Odoo 只读连接后，这里会显示真实业务通知。':status==='unauthorized'?'使用上方“访问口令”连接；独立屏可点击右上方按钮。':payload.error||'系统正在采集，请稍候。';
    if(status==='partial') bar.title=(payload.issues||[]).join('\n');else bar.title='';
    // Data timestamps never advance merely because a browser refreshed.
    const oldTime=document.querySelector('#refreshTime');if(oldTime) oldTime.textContent=payload.updatedAt?new Date(payload.updatedAt).toLocaleTimeString('zh-CN',{hour12:false}):'--:--';
    action.textContent=status==='unauthorized'?'输入口令':'刷新';
  }
  async function read(key){
    state.key=key;
    if(state.inFlight)return state.inFlight;
    state.inFlight=(async()=>{
      try{
        const token=sessionStorage.getItem('lighthouse-access')||'';
        const response=await fetch('/api/boards/'+key,{headers:token?{Authorization:'Bearer '+token}:{},signal:AbortSignal.timeout(12000),cache:'no-store'});
        if(response.status===401){const p={ok:false,status:'unauthorized'};paint(p);return p;}
        if(!response.ok)throw Error('读取失败');
        const p=await response.json();paint(p);return p;
      }catch{
        const previous=state.payload;
        const p=previous?.ok?{...previous,status:'stale',error:'无法连接灯塔，保留上次快照'}:{ok:false,status:'error',error:'无法连接灯塔，正在等待恢复'};
        paint(p);return p;
      }finally{state.inFlight=null;}
    })();return state.inFlight;
  }
  action.onclick=()=>{
    if(state.payload?.status==='unauthorized'){
      if(parent!==window)parent.postMessage('access-required',location.origin);
      else{const token=prompt('展示访问口令');if(token!==null){sessionStorage.setItem('lighthouse-access',token);location.reload();}}
    }else location.reload();
  };
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&parent!==window)parent.postMessage('exit-focus',location.origin);});
  document.addEventListener('keydown',e=>{
    if((e.key==='Enter'||e.key===' ') && e.target.matches('.risk-tile,.list-card')){e.preventDefault();e.target.click();}
  });
  // Poll cheap local snapshots; browsers never trigger Odoo collection.
  setInterval(()=>{window.Lighthouse.onRefresh?.();},15000);
  setInterval(()=>{
    const p=state.payload;
    if(p?.updatedAt && ['live','partial'].includes(p.status) && (p.ageSeconds||0)+(Date.now()-state.received)/1000>(p.refreshSeconds||180)*2)paint({...p,status:'stale'});
  },5000);
  window.Lighthouse={read,paint};
})();
