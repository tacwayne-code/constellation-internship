import {kindNames,taskNames} from './api.js';

export const documentStages={QUEUED:'待取箱',FETCHING:'取箱中',FETCH_ACK:'取箱回执处理中',WAIT_COUNT:'待盘点核对',WAIT_LOAD:'待人工装料',WAIT_PICK:'待核对取料',RETURN_QUEUED:'待返库下发',RETURNING:'返库中',RETURN_ACK:'返库回执处理中',REVIEW:'待现场核对',COMPLETED:'已完成',CANCELLED:'已取消'};
export const queryCategories=[['OUTBOUND','出库单查询'],['INBOUND','入库单查询'],['COUNT','盘点单查询'],['TASK','任务单查询']];
export const queryType=row=>row.record_type==='TASK'?'搬运任务':`${kindNames[row.kind]||row.kind}单`;
export const queryStatus=row=>(row.record_type==='TASK'?taskNames:documentStages)[row.status]||row.status;
const dayString=date=>`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
export function dateRange(days,now=new Date()){
 if(!days)return {from:'',to:''};
 return {from:dayString(new Date(now.getFullYear(),now.getMonth(),now.getDate()-days+1)),to:dayString(now)};
}
function localDay(value,next=false){
 if(!/^\d{4}-\d{2}-\d{2}$/.test(value))throw new Error('请选择有效的查询日期');
 const [year,month,day]=value.split('-').map(Number),date=new Date(year,month-1,day);
 if(dayString(date)!==value)throw new Error('请选择有效的查询日期');
 if(next)date.setDate(date.getDate()+1);
 return date.toISOString();
}
export function queryParameters(category,filter,page,size){
 const params=new URLSearchParams({category,page:String(page+1),page_size:String(size)});
 if(filter.from&&filter.to&&filter.from>filter.to)throw new Error('开始日期不能晚于结束日期');
 if(filter.from)params.set('created_from',localDay(filter.from));
 if(filter.to)params.set('created_before',localDay(filter.to,true));
 if(filter.keyword.trim())params.set('keyword',filter.keyword.trim());
 if(filter.status)params.set('status',filter.status);
 return params.toString();
}
