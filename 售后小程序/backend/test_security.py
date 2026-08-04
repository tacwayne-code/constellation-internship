"""安全加固冒烟测试：验证 SECRET_KEY 处理、限流、上传、Cookie、异常处理等"""
import io
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test_sec.db"
os.environ["ENV"] = "development"
os.environ["UPLOAD_MAX_SIZE_MB"] = "10"
# 隔离测试环境：模拟 Odoo 未配置（不依赖真实 Odoo 服务，验证 503 降级路径）
os.environ["ODOO_URL"] = ""
os.environ["ODOO_DB"] = ""
os.environ["ODOO_USERNAME"] = ""
os.environ["ODOO_PASSWORD"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from main import LOGIN_MAX_FAILURES, app  # noqa: E402

# 使用上下文管理器以触发 startup 事件（种子数据初始化）
with TestClient(app) as client:
    # 1. health 暴露 uploads_mode
    h = client.get("/health")
    assert h.status_code == 200, h.text
    assert h.json()["uploads_mode"] == "public", h.json()

    # 2. 未授权访问返回 401
    assert client.get("/workorders").status_code == 401

    # 3. 登录成功 + httpOnly Cookie 下发
    r = client.post("/auth/login", json={"username": "PD001", "password": "123456", "role": "paidan"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    set_cookie = r.headers.get("set-cookie", "")
    assert "aftersales_token=" in set_cookie and "HttpOnly" in set_cookie, set_cookie

    # 3.1 手机号登录（新登录方式，seed 数据已回填真实手机号）
    r = client.post("/auth/login", json={"phone": "13800010002", "password": "123456", "role": "paidan"})
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"phone": "13800000002", "password": "123456", "role": "engineer"})
    assert r.status_code == 200, r.text

    # 3.2 非法手机号 → 422（参数校验）
    r = client.post("/auth/login", json={"phone": "123", "password": "123456", "role": "paidan"})
    assert r.status_code == 422, r.text

    # 3.3 手机号与角色不匹配 → 400
    r = client.post("/auth/login", json={"phone": "13800000002", "password": "123456", "role": "paidan"})
    assert r.status_code == 400, r.text

    # 4. Authorization 头鉴权
    assert client.get("/workorders", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    # 5. Cookie 自动鉴权（TestClient cookie jar）
    assert client.get("/engineers").status_code == 200

    # 5.1 Odoo 客户接口：需登录态；未配置 Odoo 时返回 503
    client.cookies.clear()  # 清除 cookie → 未登录 → 401
    assert client.get("/api/odoo/customers?keyword=abc").status_code == 401
    r = client.post("/auth/login", json={"phone": "13800010002", "password": "123456", "role": "paidan"})
    assert r.status_code == 200, r.text
    r = client.get("/api/odoo/customers?keyword=abc")
    assert r.status_code in (503, 502), r.text  # 未配置 Odoo 时返回 503
    # 工程师角色访问 → 403
    r = client.post("/auth/login", json={"phone": "13800000002", "password": "123456", "role": "engineer"})
    assert r.status_code == 200, r.text
    r = client.get("/api/odoo/customers?keyword=abc")
    assert r.status_code == 403, r.text

    # 6. 上传非图片（文本伪装）被拒
    r = client.post("/api/upload", files={"file": ("evil.txt", io.BytesIO(b"<script>alert(1)</script>"), "text/plain")})
    assert r.status_code == 400, r.text

    # 7. 伪造 content_type 的 HTML 内容被拒（魔数校验兜底）
    r = client.post("/api/upload", files={"file": ("x.png", io.BytesIO(b"<html>bad</html>"), "image/png")})
    assert r.status_code == 400, r.text

    # 8. 真实 PNG 上传成功，且可通过 /uploads 访问
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 128
    r = client.post("/api/upload", files={"file": ("a.png", io.BytesIO(png), "image/png")})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith("/uploads/")
    assert client.get(url).status_code == 200

    # 9. 超大文件（>10MB）被拒 413
    big = b"\x89PNG\r\n\x1a\n" + b"A" * (11 * 1024 * 1024)
    r = client.post("/api/upload", files={"file": ("big.png", io.BytesIO(big), "image/png")})
    assert r.status_code == 413, (r.status_code, r.text[:200])

    # 10. 登录限流：连续失败达到上限后锁定
    for _ in range(LOGIN_MAX_FAILURES):
        r = client.post("/auth/login", json={"username": "PD001", "password": "wrong", "role": "paidan"})
        assert r.status_code == 400, r.text
    r = client.post("/auth/login", json={"username": "PD001", "password": "123456", "role": "paidan"})
    assert r.status_code == 429, (r.status_code, r.text)

    # 11. 全局参数校验：错误时间格式 / 非法经纬度 → 422 且不泄露堆栈
    r = client.post("/workorders/1/records", json={
        "start_time": "not-a-time", "end_time": "2026-08-03 12:00", "analysis": "x",
        "longitude": 999, "latitude": 0,
    })
    assert r.status_code == 422, r.text
    assert "errors" in r.json(), r.text

    # 12. 越权/不存在路由错误响应为 JSON（全局异常处理生效）
    r = client.get("/workorders/999999")
    assert r.status_code == 404, r.text
    assert r.headers.get("x-content-type-options") == "nosniff"

    # 13. logout 清除 cookie
    r = client.post("/auth/logout")
    assert r.status_code == 200, r.text
    assert client.get("/engineers").status_code == 401  # cookie 已清除

    print("ALL SECURITY TESTS PASSED ✅")
