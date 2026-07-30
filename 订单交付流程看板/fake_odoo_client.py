"""
FakeOdooClient - Mock Odoo XML-RPC 客户端
仅在 ODOO_MOCK_MODE=true 时使用
所有数据基于真实 Odoo 只读调查结果构建，字段与真实接口一致
"""
import logging

logger = logging.getLogger("fake_odoo")


class FakeOdooError(RuntimeError):
    pass


class FakeOdooClient:
    """
    模拟 Odoo XML-RPC 客户端
    提供与 OdooClient 相同的接口：authenticate, call, search_read, read
    返回模拟数据，不访问真实 Odoo
    """

    def __init__(self):
        self._uid = 9999  # 模拟 UID
        self._authenticated = False
        self._build_mock_data()
        logger.info("FakeOdooClient 已初始化 (MOCK MODE)")

    # ==========================================
    # Mock 数据（基于真实 Odoo 调查脱敏构建）
    # ==========================================

    def _build_mock_data(self):
        # 员工 - 基于真实 Odoo 调查
        self._employees = [
            {"id": 1, "name": "Administrator", "work_phone": False, "department_id": [1, "Administration"],
             "job_title": False, "job_id": False},
            {"id": 6, "name": "Wayne^inspiri", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 5, "name": "孟珊珊", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 4, "name": "李宁盛", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 17, "name": "杨艳桢", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 2, "name": "柴秋彦", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 18, "name": "梁嘉恩", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 3, "name": "王总", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 15, "name": "罗总", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 16, "name": "翁贻轩", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 9, "name": "陈吴琴", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 10, "name": "陈燊绮", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 14, "name": "黄庆玲", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            # Mock: 罗伟华（Odoo中不存在，Mock模式添加）
            {"id": 9001, "name": "罗伟华", "work_phone": False,
             "department_id": [2, "组装班"], "job_title": "生产工人", "job_id": [1, "操作工"]},
            # Mock: 其他默认工人
            {"id": 7, "name": "test", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
            {"id": 8, "name": "外包人员", "work_phone": False, "department_id": False,
             "job_title": False, "job_id": False},
        ]

        # 物料数据（基于真实 Odoo 调查）
        self._products = {
            "P04725": {"id": 11632, "default_code": "P04725", "name": "编带机箱",
                       "product_tmpl_id": [12977, "编带机箱"],
                       "categ_id": [1, "All"], "uom_id": [1, "pcs"], "type": "product",
                       "spec_info": "黑色:4U300", "brand_name": "淘宝"},
            "P05346": {"id": 12253, "default_code": "P05346", "name": "cpu",
                       "product_tmpl_id": [13001, "cpu"],
                       "categ_id": [5, "All / 原材料 / 堆垛机"], "uom_id": [1, "pcs"], "type": "product",
                       "spec_info": "I3-3220", "brand_name": "西门子"},
            "P05347": {"id": 12254, "default_code": "P05347", "name": "内存条",
                       "product_tmpl_id": [13002, "内存条"],
                       "categ_id": [5, "All / 原材料 / 堆垛机"], "uom_id": [1, "pcs"], "type": "product",
                       "spec_info": "DDR3-4G", "brand_name": "西门子"},
            "P05350": {"id": 12257, "default_code": "P05350", "name": "硬盘",
                       "product_tmpl_id": [13005, "硬盘"],
                       "categ_id": [5, "All / 原材料 / 堆垛机"], "uom_id": [1, "pcs"], "type": "product",
                       "spec_info": "SSD-128G", "brand_name": "西门子"},
            "P05348": {"id": 12255, "default_code": "P05348", "name": "硬盘",
                       "product_tmpl_id": [13003, "硬盘"],
                       "categ_id": [5, "All / 原材料 / 堆垛机"], "uom_id": [1, "pcs"], "type": "product",
                       "spec_info": "SSD-64G", "brand_name": "西门子"},
            "P05351": {"id": 12258, "default_code": "P05351", "name": "显卡",
                       "product_tmpl_id": [13006, "显卡"],
                       "categ_id": [5, "All / 原材料 / 堆垛机"], "uom_id": [1, "pcs"], "type": "product",
                       "spec_info": "G210", "brand_name": "西门子"},
            "P05352": {"id": 12259, "default_code": "P05352", "name": "机箱电源",
                       "product_tmpl_id": [13007, "机箱电源"],
                       "categ_id": [5, "All / 原材料 / 堆垛机"], "uom_id": [1, "pcs"], "type": "product",
                       "spec_info": "ATX-400W", "brand_name": "西门子"},
            "P05353": {"id": 12260, "default_code": "P05353", "name": "机箱风扇",
                       "product_tmpl_id": [13008, "机箱风扇"],
                       "categ_id": [5, "All / 原材料 / 堆垛机"], "uom_id": [1, "pcs"], "type": "product",
                       "spec_info": "", "brand_name": "西门子"},
            "P04726": {"id": 11633, "default_code": "P04726", "name": "分光机箱",
                       "product_tmpl_id": [12978, "分光机箱"],
                       "categ_id": [1, "All"], "uom_id": [1, "pcs"], "type": "product",
                       "spec_info": "4U-610H", "brand_name": "淘宝"},
        }

        # 生产单
        self._productions = [
            {"id": 1001, "name": "WH/MO/00001", "product_id": [11632, "编带机箱"],
             "product_qty": 10.0, "product_uom_id": [1, "pcs"],
             "bom_id": False, "state": "confirmed", "date_deadline": "2026-08-15",
             "origin": "SO001", "qty_produced": 0.0, "user_id": [1, "Administrator"]},
            {"id": 1002, "name": "WH/MO/00002", "product_id": [11633, "分光机箱"],
             "product_qty": 5.0, "product_uom_id": [1, "pcs"],
             "bom_id": False, "state": "confirmed", "date_deadline": "2026-08-20",
             "origin": "SO002", "qty_produced": 0.0, "user_id": [1, "Administrator"]},
            {"id": 1003, "name": "WH/MO/00003", "product_id": [11632, "编带机箱"],
             "product_qty": 8.0, "product_uom_id": [1, "pcs"],
             "bom_id": False, "state": "confirmed", "date_deadline": "2026-08-25",
             "origin": "SO003", "qty_produced": 2.0, "user_id": [1, "Administrator"]},
        ]

        # 工单
        self._workorders = [
            {"id": 2001, "name": "电脑装机（编带主机）", "production_id": [1001, "WH/MO/00001"],
             "workcenter_id": [101, "电脑装机（编带主机）"], "operation_id": False,
             "product_id": [11632, "编带机箱"], "state": "ready",
             "qty_production": 10.0, "qty_produced": 0.0, "qty_remaining": 10.0,
             "duration_expected": 120.0},
            {"id": 2002, "name": "电脑装机（分光主机）", "production_id": [1002, "WH/MO/00002"],
             "workcenter_id": [102, "电脑装机（分光主机）"], "operation_id": False,
             "product_id": [11633, "分光机箱"], "state": "ready",
             "qty_production": 5.0, "qty_produced": 0.0, "qty_remaining": 5.0,
             "duration_expected": 120.0},
            {"id": 2003, "name": "电脑装机（编带主机）", "production_id": [1003, "WH/MO/00003"],
             "workcenter_id": [101, "电脑装机（编带主机）"], "operation_id": False,
             "product_id": [11632, "编带机箱"], "state": "progress",
             "qty_production": 8.0, "qty_produced": 2.0, "qty_remaining": 6.0,
             "duration_expected": 120.0},
        ]

        # BOM（Mock：编带主机）
        self._tape_bom = [
            {"id": 3001, "product_id": [11632, "编带机箱"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4001, "编带主机BOM"], "sequence": 1},
            {"id": 3002, "product_id": [12253, "cpu"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4001, "编带主机BOM"], "sequence": 2},
            {"id": 3003, "product_id": [12254, "内存条"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4001, "编带主机BOM"], "sequence": 3},
            {"id": 3004, "product_id": [12257, "硬盘"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4001, "编带主机BOM"], "sequence": 4},
            {"id": 3005, "product_id": [12258, "显卡"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4001, "编带主机BOM"], "sequence": 5},
            {"id": 3006, "product_id": [12259, "机箱电源"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4001, "编带主机BOM"], "sequence": 6},
            {"id": 3007, "product_id": [12260, "机箱风扇"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4001, "编带主机BOM"], "sequence": 7},
        ]

        # BOM（Mock：分光主机）
        self._splitter_bom = [
            {"id": 3008, "product_id": [11633, "分光机箱"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4002, "分光主机BOM"], "sequence": 1},
            {"id": 3009, "product_id": [12253, "cpu"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4002, "分光主机BOM"], "sequence": 2},
            {"id": 3010, "product_id": [12254, "内存条"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4002, "分光主机BOM"], "sequence": 3},
            {"id": 3011, "product_id": [12255, "硬盘"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4002, "分光主机BOM"], "sequence": 4},
            {"id": 3012, "product_id": [12258, "显卡"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4002, "分光主机BOM"], "sequence": 5},
            {"id": 3013, "product_id": [12259, "机箱电源"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4002, "分光主机BOM"], "sequence": 6},
            {"id": 3014, "product_id": [12260, "机箱风扇"], "product_qty": 1.0,
             "product_uom_id": [1, "pcs"], "bom_id": [4002, "分光主机BOM"], "sequence": 7},
        ]

        # 库存（Mock）
        self._stock = [
            {"id": 5001, "product_id": [11632, "编带机箱"], "location_id": [8, "Stock"],
             "quantity": 50.0, "reserved_quantity": 0.0, "available_quantity": 50.0},
            {"id": 5002, "product_id": [12253, "cpu"], "location_id": [8, "Stock"],
             "quantity": 100.0, "reserved_quantity": 10.0, "available_quantity": 90.0},
            {"id": 5003, "product_id": [12254, "内存条"], "location_id": [8, "Stock"],
             "quantity": 80.0, "reserved_quantity": 5.0, "available_quantity": 75.0},
            {"id": 5004, "product_id": [12255, "硬盘"], "location_id": [8, "Stock"],
             "quantity": 60.0, "reserved_quantity": 0.0, "available_quantity": 60.0},
            {"id": 5005, "product_id": [12257, "硬盘"], "location_id": [8, "Stock"],
             "quantity": 40.0, "reserved_quantity": 5.0, "available_quantity": 35.0},
            {"id": 5006, "product_id": [12258, "显卡"], "location_id": [8, "Stock"],
             "quantity": 70.0, "reserved_quantity": 0.0, "available_quantity": 70.0},
            {"id": 5007, "product_id": [12259, "机箱电源"], "location_id": [8, "Stock"],
             "quantity": 90.0, "reserved_quantity": 0.0, "available_quantity": 90.0},
            {"id": 5008, "product_id": [12260, "机箱风扇"], "location_id": [8, "Stock"],
             "quantity": 120.0, "reserved_quantity": 0.0, "available_quantity": 120.0},
            {"id": 5009, "product_id": [11633, "分光机箱"], "location_id": [8, "Stock"],
             "quantity": 50.0, "reserved_quantity": 0.0, "available_quantity": 50.0},
        ]

        # 工序
        self._workcenters = [
            {"id": 101, "name": "电脑装机（编带主机）", "code": "pc_assembly_tape"},
            {"id": 102, "name": "电脑装机（分光主机）", "code": "pc_assembly_splitter"},
        ]

        # 库存不足产品列表（用于测试）
        self._low_stock_product_ids = {12257}  # SSD-128G 库存只有35，其中5已预留

    # ==========================================
    # 公共接口（与 OdooClient 一致）
    # ==========================================

    def authenticate(self):
        self._authenticated = True
        logger.info("FakeOdoo: 模拟认证成功")
        return self._uid

    def call(self, model, method, args=None, kwargs=None):
        if not self._authenticated:
            self.authenticate()
        args = args or []
        kwargs = kwargs or {}

        if method == "search_read":
            return self._search_read(model, args, kwargs)
        elif method == "read":
            return self._read(model, args, kwargs)
        elif method == "search":
            return self._search(model, args, kwargs)
        elif method == "search_count":
            return self._search_count(model, args, kwargs)
        elif method == "fields_get":
            return self._fields_get(model)
        else:
            raise FakeOdooError(f"FakeOdoo: 不支持的方法 {method}，只读模式")

    def search_read(self, model, domain, fields, limit=100, order=None):
        kw = {"fields": fields, "limit": limit}
        if order:
            kw["order"] = order
        return self.call(model, "search_read", [domain], kw)

    def read(self, model, ids, fields):
        if not ids:
            return []
        return self.call(model, "read", [ids], {"fields": fields})

    # ==========================================
    # 内部实现
    # ==========================================

    def _match_domain(self, record, domain):
        """检查记录是否匹配 domain"""
        if not domain:
            return True
        for condition in domain:
            if isinstance(condition, str):
                continue  # 前缀操作符
            if len(condition) == 3:
                field, op, value = condition
                rec_val = record.get(field)
                # 处理 many2one 字段
                if isinstance(rec_val, (list, tuple)) and len(rec_val) > 0:
                    rec_val = rec_val[0]

                if op == "=":
                    if rec_val != value:
                        return False
                elif op == "!=":
                    if rec_val == value:
                        return False
                elif op == "in":
                    if rec_val not in value:
                        return False
                elif op == "not in":
                    if rec_val in value:
                        return False
                elif op == "ilike":
                    if value.lower() not in str(rec_val).lower():
                        return False
                elif op == "like":
                    if value not in str(rec_val):
                        return False
                elif op == ">":
                    if not (rec_val is not None and rec_val > value):
                        return False
                elif op == "<":
                    if not (rec_val is not None and rec_val < value):
                        return False
                elif op == ">=":
                    if not (rec_val is not None and rec_val >= value):
                        return False
                elif op == "<=":
                    if not (rec_val is not None and rec_val <= value):
                        return False
        return True

    def _get_records(self, model):
        """获取模型对应的记录列表"""
        if model == "hr.employee":
            return self._employees
        elif model == "product.product":
            return list(self._products.values())
        elif model == "product.template":
            return [{"id": v["product_tmpl_id"][0], "name": v["product_tmpl_id"][1],
                     "default_code": k, "categ_id": v.get("categ_id"), "uom_id": v.get("uom_id"),
                     "type": v.get("type"), "brand_id": v.get("brand_name"),
                     "spec_info": v.get("spec_info"),
                     "description": v.get("spec_info", "")}
                    for k, v in self._products.items()]
        elif model == "mrp.production":
            return self._productions
        elif model == "mrp.workorder":
            return self._workorders
        elif model == "mrp.bom":
            return [{"id": 4001, "code": "TAPE_HOST_BOM", "product_tmpl_id": [12977, "编带机箱"],
                     "product_id": False, "product_qty": 1.0, "product_uom_id": [1, "pcs"],
                     "type": "normal", "active": True, "ready_to_produce": "all_available"},
                    {"id": 4002, "code": "SPLITTER_HOST_BOM", "product_tmpl_id": [12978, "分光机箱"],
                     "product_id": False, "product_qty": 1.0, "product_uom_id": [1, "pcs"],
                     "type": "normal", "active": True, "ready_to_produce": "all_available"}]
        elif model == "mrp.bom.line":
            # 返回所有 BOM 行
            return self._tape_bom + self._splitter_bom

        elif model == "mrp.workcenter":
            return self._workcenters
        else:
            logger.warning(f"FakeOdoo: 未知模型 {model}")
            return []

    def _search_read(self, model, args, kwargs):
        domain = args[0] if args else []
        fields = kwargs.get("fields", [])
        limit = kwargs.get("limit", 100)
        records = self._get_records(model)
        matched = [r for r in records if self._match_domain(r, domain)]
        result = matched[:limit]
        # 过滤字段
        if fields:
            filtered = []
            for r in result:
                fr = {f: r.get(f) for f in fields if f in r}
                filtered.append(fr)
            return filtered
        return result

    def _read(self, model, args, kwargs):
        ids = args[0] if args else []
        fields = kwargs.get("fields", [])
        records = self._get_records(model)
        result = [r for r in records if r.get("id") in ids]
        if fields:
            return [{f: r.get(f) for f in fields if f in r} for r in result]
        return result

    def _search(self, model, args, kwargs):
        domain = args[0] if args else []
        records = self._get_records(model)
        matched = [r for r in records if self._match_domain(r, domain)]
        return [r["id"] for r in matched]

    def _search_count(self, model, args, kwargs):
        domain = args[0] if args else []
        records = self._get_records(model)
        return len([r for r in records if self._match_domain(r, domain)])

    def _fields_get(self, model):
        # 返回基本字段定义
        base_fields = {"id": {"type": "integer", "string": "ID"},
                       "name": {"type": "char", "string": "Name"}}
        return base_fields
