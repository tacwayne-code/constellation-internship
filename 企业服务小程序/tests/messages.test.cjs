const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

function page(inbox) {
  let definition;
  let opened = 0;
  vm.runInNewContext(fs.readFileSync(path.join(root, 'pages/messages/index.js'), 'utf8'), {
    require: key => key.includes('business') ? { inbox, open: async () => { opened++; } } : { serviceWebUrl: 'https://example.com/web/' },
    Page: value => { definition = value; },
    wx: { stopPullDownRefresh() {}, showLoading() {}, hideLoading() {}, showToast() {} },
  });
  definition.setData = values => Object.assign(definition.data, values);
  return { definition, opened: () => opened };
}

test('real tasks, pagination and service entry', async () => {
  const { definition: p, opened } = page(async offset => ({ total: 2, items: [{ id: offset + 1, title: 'WO', updated_at: '2026-09-09T10:00:00Z' }] }));
  await p.load();
  assert.equal(p.data.notices.length, 1);
  await p.load({ currentTarget: { dataset: { more: true } } });
  assert.equal(p.data.notices.length, 2);
  await p.openService();
  assert.equal(opened(), 1);
});

test('error clears old tasks, does not pretend empty success', async () => {
  const { definition: p } = page(async () => { throw new Error('offline'); });
  p.data.notices = [{ id: 1 }];
  await p.load();
  assert.equal(p.data.error, 'offline');
  assert.equal(p.data.loading, false);
  assert.equal(p.data.notices.length, 0);
});

test('no ASS permission has explicit empty state', async () => {
  const { definition: p } = page(async () => ({ items: [], total: 0, noService: true }));
  await p.load();
  assert.equal(p.data.noService, true);
  assert.equal(p.data.error, '');
});

test('overlapping refresh does not issue duplicate requests', async () => {
  let resolve;
  let calls = 0;
  const { definition: p } = page(() => { calls++; return new Promise(r => { resolve = r; }); });
  const pending = p.load();
  await p.load();
  resolve({ items: [], total: 0 });
  await pending;
  assert.equal(calls, 1);
});

test('business API uses fresh SSO and in-memory bearer', async () => {
  const requests = [];
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'services/business.js'), 'utf8'), {
    module, require: key => key.includes('config') ? { serviceWebUrl: 'https://service.example/web/' } : {
      login: async () => ({ employee: { name: 'test' }, modules: ['after_sales'], tickets: { after_sales: 'fresh' } })
    }, getApp: () => ({ setSession() {} }),
    wx: { request: opts => {
      requests.push(opts);
      opts.success({ statusCode: 200, data: opts.method === 'POST' ? { access_token: 'memory-only' } : { items: [], total: 0 } });
    } },
  });
  await module.exports.inbox();
  assert.equal(requests[0].url, 'https://service.example/auth/sso/handoff');
  assert.equal(requests[0].data.ticket, 'fresh');
  assert.equal(requests[1].header.Authorization, 'Bearer memory-only');
  assert.equal(requests[1].url, 'https://service.example/inbox');
});
