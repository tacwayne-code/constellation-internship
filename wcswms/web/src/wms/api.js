let csrfToken='',deviceScope='primary',scopeRevision=0,locationDepths=new Map();
const globalPath=path=>/^\/api\/(auth|users|sites|warehouses|devices)(?:\/|$)/.test(path);
export function setDeviceScope(id){if(deviceScope===id)return;deviceScope=id;scopeRevision++;locationDepths=new Map();for(const key of Object.keys(sessionStorage))if(key.startsWith('wms-')&&key.includes('draft'))sessionStorage.removeItem(key);}
export function apiPath(path){if(globalPath(path))return path;if(!deviceScope)throw new Error('请先选择仓库和设备');return deviceScope==='primary'?path:`/api/scopes/${deviceScope}${path}`;}
export function setSession(session){csrfToken=session?.csrf_token||'';}
export function clearSession(){
 scopeRevision++;csrfToken='';locationDepths=new Map();sessionStorage.removeItem('wms-control-key');
 for(const key of Object.keys(sessionStorage))if(key.startsWith('wms-')&&key.includes('draft'))sessionStorage.removeItem(key);
}
export const hasPermission=(state,permission)=>Boolean(state?.user?.permissions.includes(permission));
// getRandomValues works on HTTP LAN origins, where randomUUID is unavailable.
export function requestId(){return [...crypto.getRandomValues(new Uint8Array(16))].map(value=>value.toString(16).padStart(2,'0')).join('');}
export async function api(path,body,{signal,silentAuth=false}={}){
 const revision=scopeRevision,scoped=!globalPath(path);
 const response=await fetch(apiPath(path),{signal,cache:'no-store',credentials:'same-origin',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-WMS-Request':'1','X-CSRF-Token':csrfToken},body:JSON.stringify(body)})});
 let value;try{value=await response.json();}catch{throw new Error('后台响应异常，请检查服务是否运行');}
 if(scoped&&revision!==scopeRevision)throw new Error('仓库或设备已切换，原请求结果已忽略');
 if(!response.ok){
  if(response.status===401&&!silentAuth&&!['/api/auth/login','/api/auth/bootstrap'].includes(path)){clearSession();window.dispatchEvent(new Event('wms-logged-out'));}
  const error=new Error(typeof value.detail==='string'?displayLocationText(value.detail):`请求未通过校验（${response.status}），请检查填写内容`);error.status=response.status;throw error;
 }
 if(path==='/api/state'&&value.scope?.device_id===deviceScope)setLocationLayouts(value);
 return value;
}
export const taskNames={QUEUED:'待下发',DISPATCHING:'下发中',SENT:'已发送待反馈',RUNNING:'执行中',REVIEW:'待现场核对',PLC_DONE:'完成回执处理中',AWAIT_PICKUP:'出口待取',AWAIT_RETURN:'待人工操作后返库',COMPLETED:'已完成',CANCELLED:'已取消'};
export const stockNames={IN_STOCK:'在库',AT_EXIT:'出口待取',AT_STATION:'交接点作业',SHIPPED:'已出库',REMOVED:'已解除占用'};
export const kindNames={INBOUND:'入库',OUTBOUND:'出库',COUNT:'盘点',MOVE:'调仓',AGING:'坐标老化'};
export const operatorLabel=actor=>actor?`${actor.display_name||actor.username}${actor.display_name&&actor.display_name!==actor.username?`（${actor.username}）`:''}`:'历史未记录';
export const formatTime=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—';
export const activeTask=task=>!['COMPLETED','CANCELLED'].includes(task.status);
// Stored IDs remain side-column-level-depth; visible codes use side-level-column.
export const displayLocation=value=>value==='STATION-1'?'交接点 1':String(value||'—').replace(/^([LR])-(\d{2})-(\d{2})-[12]$/, '$1-$3-$2');
export const depthName=value=>Number(value)===2?'双伸':'单伸';
export const boxName=item=>item?.kind==='AGING'?'不关联库存':item?.container_type==='EMPTY_BIN'?'空箱':'物料箱';
export const taskLabel=task=>task.order_id?`${kindNames[task.document_kind]}单${task.order_leg==='FETCH'?'取箱':'返库'}`:`${task.container_type==='EMPTY_BIN'?'空箱':''}${kindNames[task.kind]}`;
// Display metadata follows the accepted device snapshot; commands keep the full stored ID.
export function setLocationLayouts(state){
 const depths=new Map();
 for(const location of state.locations||[])if(location.enabled!==false)depths.set(Number(location.side),Math.max(depths.get(Number(location.side))||1,Number(location.depth)||1));
 for(const layout of state.rack_layouts||[])depths.set(Number(layout.side),Number(layout.depths));
 locationDepths=depths;
}
export function isDoubleLocation(value){const match=/^([LR])-\d{2}-\d{2}-([12])$/.exec(value||'');return Boolean(match&&(locationDepths.has(match[1]==='L'?1:2)?locationDepths.get(match[1]==='L'?1:2)===2:match[2]==='2'));}
export const storedLocation=value=>`${displayLocation(value)}${isDoubleLocation(value)?' · '+depthName(value.slice(-1)):''}`;
export function locationLabel(location){return `${displayLocation(location.id)} · ${location.side===1?'左':'右'}仓 ${location.level}层 ${location.column}列${isDoubleLocation(location.id)?' · '+depthName(location.depth):''}`;}

export const displayLocationText=value=>String(value||'').replace(/\b([LR]-\d{2}-\d{2})-([12])\b/g,(id,_,depth)=>`${displayLocation(id)}${isDoubleLocation(id)?'（'+depthName(depth)+'）':''}`);
