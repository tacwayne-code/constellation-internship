# 交付控制塔 V2 · 后端代理服务

FastAPI 代理层，负责：
- 对接 Odoo 18（XML-RPC external API）
- 统一转换为前端 S() 数据契约
- 支持 Mock 离线降级
- 预留 PLM 适配器（可插拔）

## 启动

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. 配置凭据
cp .env.example .env             # 编辑 ODOO_PASSWORD

# 3. 连通性测试
python -m scripts.test_odoo_connectivity

# 4. 启动服务
uvicorn app.main:app --reload --port 8000
```

## API 文档

启动后访问 http://localhost:8000/docs 查看 Swagger UI。
