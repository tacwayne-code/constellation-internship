import http from "node:http";
import { mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createBusinessNumberGenerator } from "../src/domain/businessNumber.js";
import { MockErpAdapter } from "../src/adapters/MockErpAdapter.js";
import { mockSeed } from "../src/mocks/seed.js";
import { users } from "../src/data.js";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const DEFAULT_DATA_FILE = join(ROOT, "server", "data", "shared-db.json");
const DEFAULT_DIST_DIR = join(ROOT, "dist");
const MAX_BODY = 5 * 1024 * 1024;
const clone = (value) => structuredClone(value);

const resources = Object.freeze({
  customers: { collection: "customers", type: "customer" },
  visits: { collection: "visits", type: "visit" },
  opportunities: { collection: "opportunities", type: "opportunity" },
  sales: { collection: "sales", type: "sale" },
  "erp-sync-records": { collection: "erpSyncRecords", type: "erpSync" },
  "audit-logs": { collection: "auditLogs", type: "audit" },
});

function initialDb(seed = mockSeed) {
  return {
    version: 4,
    revision: 1,
    customers: [],
    visits: [],
    opportunities: [],
    sales: [],
    erpSyncRecords: [],
    auditLogs: [],
    counters: {},
    ...clone(seed),
  };
}

async function loadDatabase(dataFile, seed) {
  await mkdir(dirname(dataFile), { recursive: true });
  try {
    const saved = JSON.parse(await readFile(dataFile, "utf8"));
    return { ...initialDb(seed), ...saved };
  } catch {
    return initialDb(seed);
  }
}

function writeJson(res, status, body, origin = "*") {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Headers": "Content-Type, X-CRM-Actor-Id",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    Vary: "Origin",
  });
  res.end(JSON.stringify(body));
}

async function readBody(req) {
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY)
      throw Object.assign(new Error("照片或附件过大，请压缩后重试"), {
        status: 413,
        code: "BODY_TOO_LARGE",
      });
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw Object.assign(new Error("提交的数据格式不正确"), {
      status: 400,
      code: "INVALID_JSON",
    });
  }
}

function actorFromRequest(req) {
  const actorId = String(req.headers["x-crm-actor-id"] || "");
  return Object.values(users).find((user) => user.id === actorId) || null;
}

function assertRelations(db, collection, item) {
  if (["visits", "opportunities", "sales"].includes(collection)) {
    if (!db.customers.some((customer) => customer.id === item.customerId)) {
      throw Object.assign(new Error("关联客户不存在，禁止保存孤立记录"), {
        status: 400,
        code: "CUSTOMER_NOT_FOUND",
      });
    }
  }
  if (collection === "opportunities" && item.sourceVisitId) {
    const visit = db.visits.find((row) => row.id === item.sourceVisitId);
    if (!visit || visit.customerId !== item.customerId) {
      throw Object.assign(new Error("来源拜访不存在或不属于该客户"), {
        status: 400,
        code: "VISIT_RELATION_INVALID",
      });
    }
  }
  if (collection === "sales" && item.sourceOpportunityId) {
    const opportunity = db.opportunities.find(
      (row) => row.id === item.sourceOpportunityId,
    );
    if (!opportunity || opportunity.customerId !== item.customerId) {
      throw Object.assign(new Error("来源意向不存在或不属于该客户"), {
        status: 400,
        code: "OPPORTUNITY_RELATION_INVALID",
      });
    }
  }
  if (collection === "erpSyncRecords") {
    if (!db.sales.some((sale) => sale.id === item.saleId)) {
      throw Object.assign(new Error("ERP同步记录必须关联实际销售"), {
        status: 400,
        code: "SALE_NOT_FOUND",
      });
    }
    const duplicate = db.erpSyncRecords.find(
      (row) => row.saleId === item.saleId && row.id !== item.id,
    );
    if (duplicate) return duplicate;
  }
  if (
    collection === "auditLogs" &&
    !db.customers.some((customer) => customer.id === item.customerId)
  ) {
    throw Object.assign(new Error("操作记录必须关联客户"), {
      status: 400,
      code: "AUDIT_CUSTOMER_NOT_FOUND",
    });
  }
  return null;
}

function assertNoDuplicateCustomer(db, item, excludeId = "") {
  const phone =
    item.contacts?.find((contact) => contact.isPrimary)?.phone ||
    item.contacts?.[0]?.phone ||
    "";
  const duplicate = db.customers.find(
    (customer) =>
      customer.id !== excludeId &&
      (customer.name.trim() === item.name?.trim() ||
        (phone &&
          customer.contacts?.some((contact) => contact.phone === phone))),
  );
  if (duplicate)
    throw Object.assign(
      new Error(`客户名称或联系电话已存在：${duplicate.name}`),
      { status: 409, code: "DUPLICATE_CUSTOMER" },
    );
}

async function serveStatic(req, res, distDir) {
  const pathname = decodeURIComponent(
    new URL(req.url, "http://localhost").pathname,
  );
  const relative = pathname === "/" ? "index.html" : pathname.slice(1);
  const safePath = normalize(join(distDir, relative));
  if (!safePath.startsWith(normalize(distDir))) return false;
  try {
    const info = await stat(safePath);
    if (!info.isFile()) return false;
    const types = {
      ".html": "text/html; charset=utf-8",
      ".js": "text/javascript; charset=utf-8",
      ".css": "text/css; charset=utf-8",
      ".png": "image/png",
      ".svg": "image/svg+xml",
      ".ico": "image/x-icon",
    };
    res.writeHead(200, {
      "Content-Type": types[extname(safePath)] || "application/octet-stream",
      "Cache-Control":
        extname(safePath) === ".html"
          ? "no-cache"
          : "public, max-age=31536000, immutable",
    });
    res.end(await readFile(safePath));
    return true;
  } catch {
    return false;
  }
}

export async function createCrmServer({
  dataFile = DEFAULT_DATA_FILE,
  distDir = DEFAULT_DIST_DIR,
  serveDist = false,
  seed = mockSeed,
  erpAdapter = new MockErpAdapter(),
} = {}) {
  let db = await loadDatabase(dataFile, seed);
  let mutationQueue = Promise.resolve();

  const saveDatabase = async () => {
    const tempFile = `${dataFile}.${process.pid}.tmp`;
    await writeFile(tempFile, JSON.stringify(db, null, 2), "utf8");
    await rename(tempFile, dataFile);
  };
  await saveDatabase();

  const mutate = (operation) => {
    const result = mutationQueue.then(async () => {
      const value = await operation();
      db.revision = Number(db.revision || 0) + 1;
      await saveDatabase();
      return value;
    });
    mutationQueue = result.catch(() => {});
    return result;
  };

  const nextId = (type, date = new Date()) => {
    const generator = createBusinessNumberGenerator(db.counters);
    const id = generator.next(type, date);
    db.counters = generator.snapshot();
    return id;
  };

  const server = http.createServer(async (req, res) => {
    const origin = req.headers.origin || "*";
    if (req.method === "OPTIONS") return writeJson(res, 204, {}, origin);
    const url = new URL(req.url, "http://localhost");

    try {
      if (req.method === "GET" && url.pathname === "/api/health") {
        return writeJson(
          res,
          200,
          {
            ok: true,
            storage: "SHARED_JSON",
            erpMode: erpAdapter.mode || "MOCK",
            revision: db.revision,
          },
          origin,
        );
      }

      if (
        !url.pathname.startsWith("/api/") &&
        serveDist &&
        (await serveStatic(req, res, distDir))
      )
        return;
      if (!url.pathname.startsWith("/api/"))
        return writeJson(res, 404, { message: "页面不存在" }, origin);

      const actor = actorFromRequest(req);
      if (!actor)
        return writeJson(
          res,
          401,
          { code: "EMPLOYEE_NOT_RECOGNIZED", message: "未识别到公司员工身份" },
          origin,
        );

      if (req.method === "GET" && url.pathname === "/api/meta") {
        return writeJson(
          res,
          200,
          { revision: db.revision, actor, dataScope: "ALL_EMPLOYEES" },
          origin,
        );
      }

      const erpMatch = url.pathname.match(
        /^\/api\/erp\/sales\/([^/]+)\/submit$/,
      );
      if (req.method === "POST" && erpMatch) {
        const saleId = decodeURIComponent(erpMatch[1]);
        const body = await readBody(req);
        const idempotencyKey = String(body.idempotencyKey || saleId);
        if (idempotencyKey !== saleId)
          return writeJson(
            res,
            400,
            {
              code: "IDEMPOTENCY_KEY_INVALID",
              message: "ERP幂等编号必须与实际销售业务编号一致",
            },
            origin,
          );
        const sale = db.sales.find((row) => row.id === saleId);
        const customer = sale
          ? db.customers.find((row) => row.id === sale.customerId)
          : null;
        if (!sale)
          return writeJson(
            res,
            404,
            { code: "SALE_NOT_FOUND", message: "实际销售不存在" },
            origin,
          );
        if (!customer)
          return writeJson(
            res,
            400,
            { code: "CUSTOMER_NOT_FOUND", message: "关联客户不存在" },
            origin,
          );
        if (
          ![
            "CONFIRMED",
            "ERP_PENDING",
            "ERP_SYNCING",
            "ERP_SUCCESS",
          ].includes(sale.status)
        )
          return writeJson(
            res,
            409,
            {
              code: "SALE_STATUS_INVALID",
              message: "只有已确认的实际销售才能提交ERP",
            },
            origin,
          );
        const result = await erpAdapter.submitSale(
          { ...clone(sale), customer: clone(customer) },
          { idempotencyKey },
        );
        return writeJson(
          res,
          200,
          { result, erpMode: erpAdapter.mode || "MOCK" },
          origin,
        );
      }

      if (req.method === "POST" && url.pathname === "/api/reset") {
        if (actor.role !== "销售经理")
          return writeJson(
            res,
            403,
            {
              code: "MANAGER_REQUIRED",
              message: "只有销售经理可以恢复共享示例数据",
            },
            origin,
          );
        const result = await mutate(() => {
          db = initialDb(seed);
          return { revision: db.revision };
        });
        return writeJson(res, 200, result, origin);
      }

      const match = url.pathname.match(/^\/api\/([^/]+)(?:\/([^/]+))?$/);
      const config = match && resources[match[1]];
      if (!config)
        return writeJson(
          res,
          404,
          { code: "API_NOT_FOUND", message: "接口不存在" },
          origin,
        );
      const id = match[2] ? decodeURIComponent(match[2]) : "";
      const collection = config.collection;

      if (req.method === "GET" && !id) {
        const filters = Object.fromEntries(url.searchParams.entries());
        const items = db[collection].filter((item) =>
          Object.entries(filters).every(
            ([key, value]) => !value || String(item[key] ?? "") === value,
          ),
        );
        return writeJson(
          res,
          200,
          { items: clone(items), revision: db.revision },
          origin,
        );
      }

      if (req.method === "GET" && id) {
        const item = db[collection].find((row) => row.id === id);
        return item
          ? writeJson(
              res,
              200,
              { item: clone(item), revision: db.revision },
              origin,
            )
          : writeJson(
              res,
              404,
              { code: "NOT_FOUND", message: `未找到记录：${id}` },
              origin,
            );
      }

      if (req.method === "POST" && !id) {
        const body = await readBody(req);
        const saved = await mutate(() => {
          if (collection === "customers") assertNoDuplicateCustomer(db, body);
          const relationDuplicate = assertRelations(db, collection, body);
          if (relationDuplicate) return clone(relationDuplicate);
          const now = new Date().toISOString();
          const item = {
            ...clone(body),
            id: nextId(config.type, now),
            createdAt: now,
            createdBy: actor.id,
            updatedAt: now,
            updatedBy: actor.id,
          };
          if (collection === "customers") {
            item.ownerId ||= actor.id;
            item.ownerName ||= actor.name;
          }
          db[collection].unshift(item);
          return clone(item);
        });
        return writeJson(
          res,
          201,
          { item: saved, revision: db.revision },
          origin,
        );
      }

      if (req.method === "PUT" && id) {
        const body = await readBody(req);
        const saved = await mutate(() => {
          const index = db[collection].findIndex((row) => row.id === id);
          if (index < 0)
            throw Object.assign(new Error(`未找到记录：${id}`), {
              status: 404,
              code: "NOT_FOUND",
            });
          const current = db[collection][index];
          const item = {
            ...current,
            ...clone(body),
            id,
            createdAt: current.createdAt,
            createdBy: current.createdBy,
            updatedAt: new Date().toISOString(),
            updatedBy: actor.id,
          };
          if (collection === "customers")
            assertNoDuplicateCustomer(db, item, id);
          assertRelations(db, collection, item);
          db[collection][index] = item;
          return clone(item);
        });
        return writeJson(
          res,
          200,
          { item: saved, revision: db.revision },
          origin,
        );
      }

      if (req.method === "DELETE" && id && collection === "customers") {
        const removed = await mutate(() => {
          const index = db.customers.findIndex((row) => row.id === id);
          if (index < 0)
            throw Object.assign(new Error(`未找到记录：${id}`), {
              status: 404,
              code: "NOT_FOUND",
            });
          const customer = db.customers[index];
          const relatedSales = db.sales.filter((row) => row.customerId === id);
          const saleIds = new Set(relatedSales.map((row) => row.id));
          const relatedSyncRecords = db.erpSyncRecords.filter((row) =>
            saleIds.has(row.saleId),
          );
          const protectedSaleStatuses = new Set([
            "ERP_PENDING",
            "ERP_SYNCING",
            "ERP_SUCCESS",
          ]);
          const protectedSyncStatuses = new Set([
            "PENDING",
            "SYNCING",
            "SUCCESS",
          ]);
          const hasErpBusiness =
            customer.erpCustomerId ||
            customer.erpCustomerCode ||
            relatedSales.some(
              (sale) =>
                sale.erpOrderId ||
                sale.erpOrderNo ||
                protectedSaleStatuses.has(sale.status) ||
                protectedSyncStatuses.has(sale.erpSyncStatus),
            ) ||
            relatedSyncRecords.some(
              (record) =>
                record.erpOrderId ||
                record.erpOrderNo ||
                protectedSyncStatuses.has(record.status),
            );
          if (hasErpBusiness)
            throw Object.assign(
              new Error("该客户或销售已经进入Odoo同步流程，不能级联删除"),
              {
              status: 409,
              code: "CUSTOMER_LINKED_TO_ERP",
              },
            );
          const [item] = db.customers.splice(index, 1);
          db.visits = db.visits.filter((row) => row.customerId !== id);
          db.opportunities = db.opportunities.filter(
            (row) => row.customerId !== id,
          );
          db.sales = db.sales.filter((row) => row.customerId !== id);
          db.erpSyncRecords = db.erpSyncRecords.filter(
            (row) => !saleIds.has(row.saleId),
          );
          db.auditLogs = db.auditLogs.filter((row) => row.customerId !== id);
          return clone(item);
        });
        return writeJson(
          res,
          200,
          { item: removed, revision: db.revision },
          origin,
        );
      }

      return writeJson(
        res,
        405,
        { code: "METHOD_NOT_ALLOWED", message: "请求方式不支持" },
        origin,
      );
    } catch (error) {
      if (!error.status || error.status >= 500) console.error(error);
      return writeJson(
        res,
        error.status || 500,
        {
          code: error.code || "SERVER_ERROR",
          message: error.message || "服务器异常",
        },
        origin,
      );
    }
  });

  return {
    server,
    start({ port = 0, host = "127.0.0.1" } = {}) {
      return new Promise((resolveStart, rejectStart) => {
        server.once("error", rejectStart);
        server.listen(port, host, () => {
          server.off("error", rejectStart);
          resolveStart(server.address());
        });
      });
    },
    close() {
      return new Promise((resolveClose, rejectClose) =>
        server.close((error) => (error ? rejectClose(error) : resolveClose())),
      );
    },
    snapshot: () => clone(db),
  };
}

const isMain =
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const serveDist = process.argv.includes("--serve-dist");
  const application = await createCrmServer({
    dataFile: process.env.CRM_DATA_FILE || DEFAULT_DATA_FILE,
    distDir: process.env.CRM_DIST_DIR || DEFAULT_DIST_DIR,
    serveDist,
  });
  const port = Number(process.env.CRM_PORT || (serveDist ? 4173 : 4174));
  const host = process.env.CRM_HOST || "127.0.0.1";
  await application.start({ port, host });
  console.log(`CRM共享测试服务已启动：http://${host}:${port}`);
}
