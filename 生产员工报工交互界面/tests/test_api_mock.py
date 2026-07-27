"""Mock 模式 API 测试"""
import json, urllib.request, urllib.error, os, time

os.environ["ODOO_MOCK_MODE"] = "true"
BASE = "http://127.0.0.1:8090"
tests_passed = 0
tests_failed = 0

def test(name, fn):
    global tests_passed, tests_failed
    try:
        fn()
        print(f"  ✓ {name}")
        tests_passed += 1
    except Exception as e:
        print(f"  ✗ {name}: {e}")
        tests_failed += 1

def get(path):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())

def post(path, body):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())

# 等待服务器
time.sleep(1)

print("=== Mock API 测试 ===")

# 1. 健康检查
test("健康检查", lambda: (
    get("/api/health")["ok"] == True and get("/api/health")["mode"] == "mock"
))

# 2. 只增加一个罗伟华
test("罗伟华存在于工人列表", lambda: (
    any(w["name"] == "罗伟华" for w in get("/api/workers")["data"])
))

# 3. 原有工人不受影响
test("原有6个工人仍在", lambda: (
    len([w for w in get("/api/workers")["data"] if w["id"].startswith("WK")]) == 6
))

# 4. 两个新工序正常
ops = get("/api/operations")["data"]
test("电脑装机（编带主机）存在", lambda: any(o["code"] == "pc_assembly_tape" for o in ops))
test("电脑装机（分光主机）存在", lambda: any(o["code"] == "pc_assembly_splitter" for o in ops))
test("原有5个工序不变", lambda: (
    all(any(o["code"] == c for o in ops) for c in ["assembly", "testing", "qc", "packing", "debug"])
))

# 5. 两类 BOM 正确
tape_bom = get("/api/bom?hostType=tape")["data"]
splitter_bom = get("/api/bom?hostType=splitter")["data"]
test("编带 BOM 7个物料", lambda: len(tape_bom) == 7)
test("分光 BOM 7个物料", lambda: len(splitter_bom) == 7)
test("编带BOM含P04725", lambda: any(i["defaultCode"] == "P04725" for i in tape_bom))
test("分光BOM含P04726", lambda: any(i["defaultCode"] == "P04726" for i in splitter_bom))
test("编带BOM不含P04726", lambda: not any(i["defaultCode"] == "P04726" for i in tape_bom))
test("分光BOM不含P04725", lambda: not any(i["defaultCode"] == "P04725" for i in splitter_bom))
test("编带BOM含SSD-128G(P05350)", lambda: any(i["defaultCode"] == "P05350" for i in tape_bom))
test("分光BOM含SSD-64G(P05348)", lambda: any(i["defaultCode"] == "P05348" for i in splitter_bom))

# 6. p04725 大小写不变
test("P04725保持大写不变", lambda: any(i["defaultCode"] == "P04725" for i in tape_bom))

# 7. BOM 字段完整
item = tape_bom[0]
test("BOM字段: bomLineId", lambda: "bomLineId" in item)
test("BOM字段: productId", lambda: "productId" in item)
test("BOM字段: defaultCode", lambda: "defaultCode" in item)
test("BOM字段: name", lambda: "name" in item)
test("BOM字段: specification", lambda: "specification" in item)
test("BOM字段: uomName", lambda: "uomName" in item)
test("BOM字段: bomQty", lambda: "bomQty" in item)
test("BOM字段: categoryName", lambda: "categoryName" in item)
test("BOM字段: brandSupplierName", lambda: "brandSupplierName" in item)
test("BOM字段: availableQty", lambda: "availableQty" in item)

# 8. 工单
wos = get("/api/workorders")["data"]
test("工单列表非空", lambda: len(wos) > 0)
test("工单包含基本字段", lambda: all(k in wos[0] for k in ["workorderId", "productionId", "state", "remainingQty"]))

# 9. 无效数量不能提交
r = post("/api/reports", {
    "workerId": "WK001", "workerName": "张建国",
    "operation": "assembly", "qty": 0, "date": "2026-07-27", "time": "17:00",
})
test("无效数量报工被拒绝", lambda: r["ok"] == False and "数量" in r.get("error", ""))

# 10. 伪造 material 拒绝
r = post("/api/reports", {
    "workerId": "LOCAL_LWH", "workerName": "罗伟华",
    "operation": "pc_assembly_tape", "qty": 1, "date": "2026-07-27", "time": "17:00",
    "materials": [{"productId": 99999, "bomLineId": 99999, "defaultCode": "P99999", "actualQty": 1}],
})
test("伪造物料编码被拒绝", lambda: r["ok"] == False and "不属于" in r.get("error", ""))

# 11. 合法报工成功
r = post("/api/reports", {
    "workerId": "LOCAL_LWH", "workerName": "罗伟华",
    "productionId": "1001", "workorderId": "2001", "orderId": "2001",
    "operation": "pc_assembly_tape", "qty": 1, "date": "2026-07-27", "time": "17:00",
    "materials": [
        {"productId": 11632, "bomLineId": 3001, "defaultCode": "P04725", "actualQty": 1, "uomId": 1},
        {"productId": 12253, "bomLineId": 3002, "defaultCode": "P05346", "actualQty": 1, "uomId": 1},
        {"productId": 12254, "bomLineId": 3003, "defaultCode": "P05347", "actualQty": 1, "uomId": 1},
        {"productId": 12257, "bomLineId": 3004, "defaultCode": "P05350", "actualQty": 1, "uomId": 1},
        {"productId": 12258, "bomLineId": 3005, "defaultCode": "P05351", "actualQty": 1, "uomId": 1},
        {"productId": 12259, "bomLineId": 3006, "defaultCode": "P05352", "actualQty": 1, "uomId": 1},
        {"productId": 12260, "bomLineId": 3007, "defaultCode": "P05353", "actualQty": 1, "uomId": 1},
    ],
    "idempotencyKey": "api-test-key-002",
})
test("合法报工成功", lambda: r["ok"] == True)
test("Mock元数据正确", lambda: r["meta"]["mode"] == "mock" and "模拟" in r["meta"]["message"])

# 12. 幂等测试
r2 = post("/api/reports", {
    "workerId": "LOCAL_LWH", "workerName": "罗伟华",
    "productionId": "1001", "workorderId": "2001", "orderId": "2001",
    "operation": "pc_assembly_tape", "qty": 1, "date": "2026-07-27", "time": "17:00",
    "materials": [
        {"productId": 11632, "bomLineId": 3001, "defaultCode": "P04725", "actualQty": 1, "uomId": 1},
    ],
    "idempotencyKey": "api-test-key-002",
})
test("幂等请求返回已有结果", lambda: r2["ok"] == True and r2["meta"]["source"] == "idempotent_replay")

# 13. 报工统计
stats = get("/api/report-stats")
test("报工统计正确", lambda: stats["ok"] == True)
test("统计包含meta", lambda: stats.get("meta", {}).get("mode") == "mock")

# 14. 原看板
dashboard = get("/api/dashboard")
test("原看板正常返回", lambda: dashboard["ok"] == True)

# 15. Mock 标记
test("Dashboard meta含mode", lambda: dashboard["data"]["meta"]["mode"] == "mock")

# 结果
print(f"\n=== {tests_passed}/{tests_passed + tests_failed} 通过 ===")
if tests_failed > 0:
    print(f"{tests_failed} 个测试失败!")
    exit(1)
else:
    print("全部测试通过!")
