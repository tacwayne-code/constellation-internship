const incoming = new URLSearchParams(location.hash.slice(1)).get('ticket');
let ticket = incoming || sessionStorage.getItem('service-request-ticket') || '';
if (incoming) sessionStorage.setItem('service-request-ticket', incoming);
const listMode = new URLSearchParams(location.search).get('view') === 'mine';
history.replaceState(null, '', location.pathname + location.search);
const form = document.getElementById('request-form');
const submitButton = document.getElementById('submit');
const result = document.getElementById('result');
let requestId = crypto.randomUUID();
let submitted = false;
let attemptedPayload = null;
async function request(method, body, path = '') {
  if (!ticket) throw new Error('请返回 CRM 或售后服务，从报备入口重新进入');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch('/service-requests' + path, { method, signal: controller.signal,
      headers: { 'Content-Type': 'application/json', 'X-Request-Ticket': ticket },
      ...(body ? { body: JSON.stringify(body) } : {}) });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : '提交内容有误，请核对必填字段');
      error.status = response.status;
      if (response.status === 401) { ticket = ''; sessionStorage.removeItem('service-request-ticket'); }
      throw error;
    }
    return data;
  } finally { clearTimeout(timer); }
}
const names = { pending: '等待派单', assigned: '已派单，等待接单', processing: '维修处理中', done: '已完成', rejected: '已拒绝' };
let offset = 0, loadingList = false, activeOrderId = null;
const timeText = value => value ? new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z').toLocaleString('zh-CN', {hour12:false}) : '未记录';
function element(tag, text) { const el = document.createElement(tag); el.textContent = text; return el; }
async function showDetail(id) {
  activeOrderId = id;
  const panel = document.getElementById('detail'); panel.hidden = false; panel.replaceChildren(element('p','正在加载工单…')); panel.scrollIntoView({behavior:'smooth'});
  try {
    const item = await request('GET', null, '/' + id);
    panel.replaceChildren(element('h2',item.customer_name),element('p',item.order_no),element('h3',names[item.status] || item.status),
      element('p','承派工程师：' + (item.engineer_name || '等待指派')), element('p','设备：' + item.device_name),
      element('p','服务地址：' + (item.address || '未填写')),element('p','故障描述：' + item.fault_desc),element('h3','工单流程记录'));
    const contacts = element('section',''); panel.append(contacts);
    window.mountOrderContacts(contacts, item.contacts || [], body => request('POST', body, '/' + id + '/contacts'), !['done','rejected'].includes(item.status));
    if (!item.history_complete) panel.append(element('p','此历史工单的早期流转时间未保存，以下仅展示已有记录。'));
    const timeline = element('ol','');
    item.timeline.forEach(e => timeline.append(element('li',`${e.action} · ${timeText(e.at)}${e.engineer_name ? ' · ' + e.engineer_name : ''}${e.status ? ' · ' + (names[e.status] || e.status) : ''}`)));
    panel.append(timeline,element('h3','维修记录'));
    if (!item.records.length) panel.append(element('p','暂无维修记录'));
    item.records.forEach(r => panel.append(element('p',`${timeText(r.submitted_at)} · ${r.analysis || '未填写维修说明'}${r.start_time ? ' · 开始：' + r.start_time : ''}${r.end_time ? ' · 结束：' + r.end_time : ''}`)));
  } catch(error) { panel.replaceChildren(element('p',error.message)); }
}
async function refresh(append = false) {
  if (loadingList) return; loadingList = true;
  const status = document.getElementById('list-status'), more = document.getElementById('more');
  status.textContent = '正在加载…'; more.disabled = true;
  try {
    const nextOffset = append ? offset : 0;
    const data = await request('GET', null, '?offset=' + nextOffset + '&limit=20');
    const list = document.getElementById('list'); if (!append) list.replaceChildren();
    data.items.forEach(item => {
      const li = element('li',''); const button = element('button',`${item.customer_name} · ${names[item.status] || item.status}`);
      button.type='button'; button.onclick=()=>showDetail(item.id);
      li.append(button, element('p',item.order_no), element('p','承派：' + (item.engineer_name || '等待指派'))); list.append(li);
    });
    offset = nextOffset + data.items.length;
    more.hidden = offset >= data.total;
    status.textContent = data.total ? `本人提交 ${data.total} 条，已显示 ${offset} 条。点击工单查看完整进度。` : '暂无本人提交的工单';
  } catch (error) { status.textContent = error.name === 'AbortError' ? '加载超时，请重试' : error.message; }
  finally { loadingList = false; more.disabled = false; }
}
function switchView(mine) {
  activeOrderId = null;
  form.hidden = mine; document.getElementById('my-orders').hidden = !mine;
  document.getElementById('detail').hidden = true;
  if (mine) refresh();
}
document.getElementById('show-form').onclick = () => switchView(false);
document.getElementById('show-list').onclick = () => switchView(true);
document.getElementById('more').onclick = () => refresh(true);
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (submitButton.disabled || submitted) return;
  // Retries preserve the original payload and key, even after a lost response.
  attemptedPayload ||= { ...Object.fromEntries(new FormData(form)), request_id: requestId };
  [...form.elements].forEach(element => { if (element !== submitButton) element.disabled = true; });
  submitButton.disabled = true;
  result.textContent = '正在提交…';
  try {
    const data = await request('POST', attemptedPayload);
    submitted = true;
    result.textContent = `报备已保存：${data.order_no}。请等待派单员安排，不要重复提交。`;
    submitButton.textContent = '已提交';
    switchView(true);
  } catch (error) {
    if (error.status === 422) {
      attemptedPayload = null;
      [...form.elements].forEach(element => { element.disabled = false; });
    }
    result.textContent = error.name === 'AbortError' ? '提交结果暂未确认，可点原按钮重试同一份内容，请勿重复建单。' : error.message;
    submitButton.disabled = false;
  }
});
document.getElementById('back').onclick = () => history.back();
document.getElementById('refresh').onclick = async () => { await refresh(); if (activeOrderId !== null) await showDetail(activeOrderId); };
switchView(listMode);
