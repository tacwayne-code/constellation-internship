import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createCrmServer } from "../server/server.mjs";

const salesHeaders = {
  "Content-Type": "application/json",
  "X-CRM-Actor-Id": "USR-00018",
};
const managerHeaders = {
  "Content-Type": "application/json",
  "X-CRM-Actor-Id": "USR-00001",
};

test("共享API：不同员工可读取同一数据，修改可持久化且业务编号由服务端生成", async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "crm-shared-api-"));
  const dataFile = join(directory, "db.json");
  let app = await createCrmServer({ dataFile });
  let address = await app.start();
  let baseUrl = `http://127.0.0.1:${address.port}/api`;
  context.after(async () => {
    if (app.server.listening) await app.close();
    await rm(directory, { recursive: true, force: true });
  });

  let response = await fetch(`${baseUrl}/customers`, { headers: salesHeaders });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).items.length, 4);

  response = await fetch(`${baseUrl}/customers`, {
    method: "POST",
    headers: salesHeaders,
    body: JSON.stringify({
      name: "【测试】共享客户",
      contacts: [{ name: "共享联系人", phone: "18800000001", isPrimary: true }],
      address: "测试地址",
      ownerId: "USR-00018",
      ownerName: "王晨",
    }),
  });
  assert.equal(response.status, 201);
  const created = (await response.json()).item;
  assert.match(created.id, /^CUS-\d{8}-\d{4}$/);
  assert.equal(created.createdBy, "USR-00018");

  response = await fetch(`${baseUrl}/customers/${created.id}`, {
    method: "PUT",
    headers: managerHeaders,
    body: JSON.stringify({ note: "由另一名员工更新" }),
  });
  assert.equal(response.status, 200);
  const updated = (await response.json()).item;
  assert.equal(updated.note, "由另一名员工更新");
  assert.equal(updated.updatedBy, "USR-00001");

  await app.close();
  app = await createCrmServer({ dataFile });
  address = await app.start();
  baseUrl = `http://127.0.0.1:${address.port}/api`;
  response = await fetch(`${baseUrl}/customers/${created.id}`, {
    headers: salesHeaders,
  });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).item.note, "由另一名员工更新");

  response = await fetch(`${baseUrl}/reset`, {
    method: "POST",
    headers: salesHeaders,
  });
  assert.equal(response.status, 403);
  response = await fetch(`${baseUrl}/reset`, {
    method: "POST",
    headers: managerHeaders,
  });
  assert.equal(response.status, 200);
  response = await fetch(`${baseUrl}/customers`, { headers: managerHeaders });
  assert.equal((await response.json()).items.length, 4);
});

test("共享API：禁止保存未关联客户的孤立记录", async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "crm-shared-relations-"));
  const app = await createCrmServer({ dataFile: join(directory, "db.json") });
  const address = await app.start();
  context.after(async () => {
    await app.close();
    await rm(directory, { recursive: true, force: true });
  });
  const response = await fetch(`http://127.0.0.1:${address.port}/api/visits`, {
    method: "POST",
    headers: salesHeaders,
    body: JSON.stringify({
      customerId: "MISSING",
      occurredAt: "2026-07-16T10:00",
      result: "测试",
    }),
  });
  assert.equal(response.status, 400);
  assert.match((await response.json()).message, /孤立记录/);
});

test("共享API：级联删除CRM测试链路，但保护Odoo关联客户", async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "crm-customer-delete-"));
  const app = await createCrmServer({ dataFile: join(directory, "db.json") });
  const address = await app.start();
  context.after(async () => {
    await app.close();
    await rm(directory, { recursive: true, force: true });
  });
  const baseUrl = `http://127.0.0.1:${address.port}/api`;
  let response = await fetch(`${baseUrl}/customers`, {
    method: "POST",
    headers: salesHeaders,
    body: JSON.stringify({
      name: "【测试】API待删除客户",
      contacts: [{ name: "联系人", phone: "18800008888" }],
      address: "测试地址",
    }),
  });
  const customer = (await response.json()).item;
  response = await fetch(`${baseUrl}/visits`, {
    method: "POST",
    headers: salesHeaders,
    body: JSON.stringify({
      customerId: customer.id,
      occurredAt: "2026-07-17T10:00",
      result: "级联删除验证",
    }),
  });
  const visit = (await response.json()).item;
  response = await fetch(`${baseUrl}/customers/${customer.id}`, {
    method: "DELETE",
    headers: salesHeaders,
  });
  assert.equal(response.status, 200);
  response = await fetch(`${baseUrl}/visits/${visit.id}`, {
    headers: salesHeaders,
  });
  assert.equal(response.status, 404);

  response = await fetch(`${baseUrl}/customers`, {
    method: "POST",
    headers: salesHeaders,
    body: JSON.stringify({
      name: "【测试】API已关联Odoo客户",
      contacts: [{ name: "联系人", phone: "18800008887" }],
      address: "测试地址",
      erpCustomerId: "ODOO-8001",
    }),
  });
  const odooCustomer = (await response.json()).item;
  response = await fetch(`${baseUrl}/customers/${odooCustomer.id}`, {
    method: "DELETE",
    headers: salesHeaders,
  });
  assert.equal(response.status, 409);
  assert.equal((await response.json()).code, "CUSTOMER_LINKED_TO_ERP");
});
