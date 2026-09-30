const KEY='wms-control-key';
export const getKey=()=>sessionStorage.getItem(KEY)||'';
export const saveKey=value=>value?sessionStorage.setItem(KEY,value):sessionStorage.removeItem(KEY);
// getRandomValues works on HTTP LAN origins, where randomUUID is unavailable.
export function requestId(){return [...crypto.getRandomValues(new Uint8Array(16))].map(value=>value.toString(16).padStart(2,'0')).join('');}
export async function api(path,body,{signal,key=getKey()}={}){
 const response=await fetch(path,{signal,cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Control-Key':key},body:JSON.stringify(body)})});
 let value;try{value=await response.json();}catch{throw new Error('后台响应异常，请检查服务是否运行');}
 if(!response.ok){
  if(response.status===401){saveKey('');window.dispatchEvent(new Event('wms-locked'));}
  throw new Error(typeof value.detail==='string'?displayLocationText(value.detail):`请求未通过校验（${response.status}），请检查填写内容`);
 }
 return value;
}
export const taskNames={QUEUED:'待下发',DISPATCHING:'下发中',SENT:'已发送待反馈',RUNNING:'执行中',REVIEW:'待现场核对',PLC_DONE:'完成回执处理中',AWAIT_PICKUP:'出口待取',AWAIT_RETURN:'待人工操作后返库',COMPLETED:'已完成',CANCELLED:'已取消'};
export const stockNames={IN_STOCK:'在库',AT_EXIT:'出口待取',AT_STATION:'交接点作业',SHIPPED:'已出库',REMOVED:'已解除占用'};
export const kindNames={INBOUND:'入库',OUTBOUND:'出库'};
export const formatTime=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—';
export const activeTask=task=>!['COMPLETED','CANCELLED'].includes(task.status);
// Stored IDs remain side-column-level-depth; visible codes use side-level-column.
export const displayLocation=value=>value==='STATION-1'?'交接点 1':String(value||'—').replace(/^([LR])-(\d{2})-(\d{2})-[12]$/, '$1-$3-$2');
export const depthName=value=>Number(value)===2?'双伸':'单伸';
export const boxName=item=>item?.container_type==='EMPTY_BIN'?'空箱':'物料箱';
export const taskLabel=task=>task.order_id?`${kindNames[task.document_kind]}单${task.order_leg==='FETCH'?'取箱':'返库'}`:`${task.container_type==='EMPTY_BIN'?'空箱':''}${kindNames[task.kind]}`;
export const storedLocation=value=>`${displayLocation(value)}${/^[LR]-\d{2}-\d{2}-[12]$/.test(value||'')?' · '+depthName(value.slice(-1)):''}`;
export function locationLabel(location){return `${displayLocation(location.id)} · ${location.side===1?'左':'右'}仓 ${location.level}层 ${location.column}列 · ${depthName(location.depth)}`;}

export const displayLocationText=value=>String(value||'').replace(/\b([LR]-\d{2}-\d{2})-([12])\b/g,(id,_,depth)=>`${displayLocation(id)}（${depthName(depth)}）`);
