const ticket = new URLSearchParams(location.hash.slice(1)).get('ticket') || '';
history.replaceState(null, '', location.pathname);
const form = document.getElementById('request-form');
const submitButton = document.getElementById('submit');
const result = document.getElementById('result');
let requestId = crypto.randomUUID();
let submitted = false;
let attemptedPayload = null;
async function request(method, body) {
  if (!ticket) throw new Error('请返回 CRM 或售后服务，从报备入口重新进入');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch('/service-requests', { method, signal: controller.signal,
      headers: { 'Content-Type': 'application/json', 'X-Request-Ticket': ticket },
      ...(body ? { body: JSON.stringify(body) } : {}) });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : '提交内容有误，请核对必填字段');
      error.status = response.status;
      throw error;
    }
    return data;
  } finally { clearTimeout(timer); }
}
async function refresh() {
  const status = document.getElementById('list-status');
  status.textContent = '正在加载…';
  try {
    const data = await request('GET');
    document.getElementById('list').replaceChildren(...data.items.map(item => {
      const li = document.createElement('li');
      const names = { pending: '等待派单', assigned: '已派单', processing: '处理中', done: '已完成', rejected: '已拒绝' };
      li.textContent = `${item.order_no} · ${item.customer_name} · ${names[item.status] || item.status}`;
      return li;
    }));
    status.textContent = data.items.length ? '仅显示本人最近 50 条报备' : '暂无本人报备';
  } catch (error) { status.textContent = error.name === 'AbortError' ? '加载超时，请重试' : error.message; }
}
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
    await refresh();
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
document.getElementById('refresh').onclick = refresh;
refresh();
