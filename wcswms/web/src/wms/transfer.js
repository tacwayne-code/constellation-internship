import {activeTask} from './api.js';

export const taskPermission=task=>['MOVE','AGING'].includes(task.kind)?'inventory':(task.document_kind||task.kind).toLowerCase();
const touches=(task,id)=>task.location===id||task.target_location===id;
const idAtDepth=(location,depth)=>location.id.replace(/-[12]$/,`-${depth}`);
export function transferSources(state){
 return state.stock.filter(box=>{const location=state.locations.find(row=>row.id===box.location);return box.status==='IN_STOCK'&&!box.reserved&&location?.enabled&&location.verified&&!location.reserved&&!location.access_error&&!state.tasks.some(task=>activeTask(task)&&(task.barcode===box.barcode||touches(task,box.location)));});
}
export function transferTargets(state,box){
 if(!box)return [];
 return state.locations.filter(location=>{
  if(location.id===box.location||!location.enabled||!location.verified||location.reserved||state.stock.some(item=>item.location===location.id&&item.status==='IN_STOCK')||state.tasks.some(task=>activeTask(task)&&touches(task,location.id)))return false;
  if(location.depth===2){const front=state.locations.find(row=>row.id===idAtDepth(location,1));if(!front?.enabled||!front.verified||front.reserved||state.stock.some(item=>item.barcode!==box.barcode&&item.location===front.id&&item.status==='IN_STOCK')||state.tasks.some(task=>activeTask(task)&&touches(task,front.id)))return false;}
  else if(state.tasks.some(task=>activeTask(task)&&touches(task,idAtDepth(location,2))))return false;
  return true;
 });
}
export const transferMatches=(row,query)=>query.trim().toLowerCase().split(/\s+/).every(term=>row.toLowerCase().includes(term));
