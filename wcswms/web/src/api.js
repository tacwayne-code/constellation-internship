export async function api(path, body, options = {}) {
  let response;
  try { response = await fetch(path, {...options, ...(body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}); }
  catch(error) { if(error.name === 'AbortError') throw error; throw new Error('无法连接本机服务，请检查 Python 服务是否运行'); }
  let data;
  try { data = await response.json(); } catch { throw new Error(`服务响应异常（HTTP ${response.status}），请确认应用已启动`); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（HTTP ${response.status}），请检查输入或服务状态`);
  return data;
}
export const kinds={INBOUND:'入库',OUTBOUND:'出库',TRANSFER:'移库'};
export const statuses={QUEUED:'排队中',RUNNING:'执行中',BLOCKED:'故障挂起',RECOVERY_REQUIRED:'待恢复',COMPLETED:'已完成',CANCELLED:'已取消'};
export const phases={MOVE_SOURCE:'前往取货位',PICK:'伸叉取货',RETRACT_PICK:'取货收叉',MOVE_TARGET:'前往放货位',PLACE:'伸叉放货',RETRACT_PLACE:'放货收叉',DONE:'任务完成',IDLE:'等待任务'};
export const time=(s)=>new Date(s).toLocaleTimeString('zh-CN',{hour12:false});
