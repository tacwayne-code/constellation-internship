"""清单导入：物料识别 + 供应商推荐 + 批量建采购单 + 回写供应商主数据

数据源：Odoo 标准模型（product.template / product.product / res.partner /
        product.supplierinfo / purchase.order / purchase.order.line）

核心闭环：
  1. 导入清单（名称 + 数量）→ 提取「类型词 + 规格」
  2. 类型词 + 规格（name + spec_info）双维度匹配 product.template
  3. 供应商推荐：supplierinfo 首选 + 历史采购频次 + supplier_rank 评分
  4. 批量建 PO（按供应商聚合，priority=1 紧急 + date_planned 交期 + partner）
  5. 建单成功后回写 product.supplierinfo（供应商/价格/交期），形成自我增强闭环
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from app.services.odoo.client import OdooClient
from app.services.odoo.models import (
    MODEL_PARTNER,
    MODEL_PRODUCT,
    MODEL_PURCHASE,
    MODEL_PURCHASE_LINE,
    PRIORITY_NORMAL,
    PRIORITY_URGENT,
)

logger = logging.getLogger(__name__)

# 兜底供应商：清单导入识别后无确定供应商时，默认使用该供应商
FALLBACK_PARTNER_NAME = "淘宝电商公司"


async def _ensure_fallback_partner(client: OdooClient) -> int | None:
    """查找兜底供应商「淘宝电商公司」（按名称精确匹配），不存在则创建。

    返回 partner_id；失败返回 None。
    """
    records = await client.search_read(
        MODEL_PARTNER, [["name", "=", FALLBACK_PARTNER_NAME]], ["id"], limit=1,
    )
    if records:
        return records[0]["id"]
    try:
        pid = await client.create(MODEL_PARTNER, {"name": FALLBACK_PARTNER_NAME, "is_company": True})
        try:
            await client.write(MODEL_PARTNER, [pid], {"supplier_rank": 1})
        except Exception:  # noqa: BLE001
            pass
        return pid
    except Exception as e:  # noqa: BLE001
        logger.warning("创建兜底供应商失败: %s", e)
        return None


# 标准件类型词库：按长度降序，匹配时取最长命中（避免「内六角」误截「内六角圆柱头螺钉」）
TYPE_WORDS = [
    "内六角圆柱头螺钉", "内六角平头螺钉", "内六角圆头螺钉", "内六角沉头螺钉",
    "弹性圆柱销卷制轻型", "弹性圆柱销", "不锈钢圆柱销钉",
    "四集双头集电器", "集电器",
    "滑触线拉紧器", "滑触线吊架", "滑触线夹", "滑触线",
    "平垫圈", "弹性垫圈", "六角螺母", "化学螺栓", "拉爆螺母",
    "平键", "挡圈", "弹簧垫圈",
]

# 用于从名称中提取规格的正则（在规范化后字符串上匹配）
_SPEC_RE = re.compile(
    r"(?:M\d+(?:[x*]\d+){0,2}"          # M16 / M12*100
    r"|φ\d+(?:[x*]\d+)?"                 # φ4 / φ4*8
    r"|\d+(?:[x*.]\d+)+"                 # 16*3 / 16*3.1
    r"|\d+\s*(?:mm|米|平方|m2|㎡|m²)?"    # 14248mm / 110米 / 8平方
    r"|\d+)",
    re.IGNORECASE,
)


def _fullwidth_to_halfwidth(text: str) -> str:
    """全角字符 → 半角（含全角空格、全角字母数字符号）"""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def normalize(text: Any) -> str:
    """名称/规格统一：全角转半角、符号统一、去空格、小写"""
    if not text:
        return ""
    s = _fullwidth_to_halfwidth(str(text))
    s = s.replace("×", "*").replace("x", "*").replace("X", "*")
    s = s.replace("．", ".").replace("。", ".").replace("，", ",")
    s = s.replace("㎡", "m2").replace("m²", "m2")
    s = re.sub(r"\s+", "", s)
    return s.lower()


def extract_type_spec(raw_name: str) -> tuple[str, str]:
    """从清单名称提取 (类型词, 规格)。

    - 类型词：命中 TYPE_WORDS 的最长词
    - 规格：去掉类型词后的剩余部分（含前缀/后缀，如 M12X100化学螺栓 → 化学螺栓 + M12X100）
    类型词库未命中时，用正则提取规格、剩余作为类型词。
    """
    name = normalize(raw_name)
    for tw in sorted(TYPE_WORDS, key=len, reverse=True):
        if tw in name:
            spec = name.replace(tw, "", 1).strip(" *-")
            return tw, spec
    # 兜底：正则提取规格
    specs = _SPEC_RE.findall(name)
    spec = specs[-1] if specs else ""
    type_word = _SPEC_RE.sub("", name).strip(" *-")
    return type_word or name, spec


def _spec_similarity(a: str, b: str) -> float:
    """规格相似度：相等 1.0 / 前缀匹配 0.6 / 包含 0.4 / 否则 0"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a.startswith(b) or b.startswith(a):
        return 0.6
    if a in b or b in a:
        return 0.4
    return 0.0


def _match_candidates(
    type_word: str,
    spec: str,
    products: list[dict],
    full_name: str = "",
) -> tuple[dict | None, int, list[dict]]:
    """在已加载的产品里做「类型词 + 规格」双维度匹配。

    返回 (精确命中产品 | None, 分数, 候选列表)。

    候选集合 = 名称精确等于类型词 ∪ 类型词是名称子串。
    两者取并集（而非二选一），避免出现「存在名称恰为类型词的通用件」时，
    名称内嵌规格的型号（如「平垫圈 16×3」）被漏掉、导致待选择里没有所需型号。
    """
    if not type_word:
        return None, 0, []

    def pname(p: dict) -> str:
        return normalize(p.get("name"))

    # 1) 候选并集（按 id 去重）：仅「名称精确等于类型词」∪「类型词是名称子串」。
    #    不做反向子串（name in type_word），否则通用名产品（如「配件」）会误匹配
    #    任何含该词的输入（如「特殊配件XYZ」），导致识别不准确。
    seen: dict[int, dict] = {}
    for p in products:
        name = pname(p)
        if not name:
            continue
        if name == type_word or type_word in name:
            seen[p["id"]] = p

    # 2) 名称维度无候选时，按编号（default_code）兜底，支持「型号=编码」的清单
    if not seen and full_name:
        for p in products:
            code = normalize(p.get("default_code"))
            if code and (code == full_name or code in full_name or full_name in code):
                seen[p["id"]] = p

    if not seen:
        return None, 0, []
    candidates = list(seen.values())

    # 3) 规格相似度：spec_info 优先，名称内嵌规格兜底；再偏好「全名精确相等」
    def sim(p: dict) -> float:
        return max(
            _spec_similarity(normalize(p.get("spec_info")), spec),
            _spec_similarity(pname(p), spec),
        )

    def exact_full(p: dict) -> float:
        return 1.0 if full_name and pname(p) == full_name else 0.0

    if spec:
        # 精确命中：spec_info 完全相等，或产品名称与整行输入完全一致 → auto
        for p in candidates:
            if normalize(p.get("spec_info")) == spec:
                return p, 100, candidates
        if full_name:
            for p in candidates:
                if pname(p) == full_name:
                    return p, 100, candidates
        # 未精确命中：按 (规格相似度, 全名相等, 名称长度) 降序，供人工选
        candidates = sorted(
            candidates,
            key=lambda p: (-sim(p), -exact_full(p), len(pname(p))),
        )
        return None, 70, candidates

    # 无规格：单候选直接命中，多候选返回列表（全名精确相等优先）
    if len(candidates) == 1:
        return candidates[0], 95, candidates
    candidates = sorted(candidates, key=lambda p: (-exact_full(p), len(pname(p))))
    return None, 80, candidates


async def load_product_index(client: OdooClient) -> list[dict]:
    """一次性加载 product.template 索引（id/name/default_code/spec_info/purchase_ok）。

    注意：spec_info 由第三方模块 product_ux（好易管软件）提供，真实库可能未安装该模块。
    因此先探测：spec_info 不存在时降级为仅标准字段（name/default_code），
    避免 search_read 直接抛「Field spec_info does not exist」导致清单导入崩溃。
    """
    base_fields = ["id", "name", "default_code", "purchase_ok"]
    try:
        products = await client.search_read(
            MODEL_PRODUCT, [["active", "=", True]],
            base_fields + ["spec_info"], limit=10000,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("product.template.spec_info 不存在（未安装 product_ux），降级为 name/default_code 匹配：%s", e)
        products = await client.search_read(
            MODEL_PRODUCT, [["active", "=", True]], base_fields, limit=10000,
        )
        for p in products:
            p["spec_info"] = ""
    return products


def _infer_code(name: str, matched_partner: dict | None = None) -> str:
    """从名称推测编号：
    - 先用清单里的 code 字段（若有）
    - 切分后去掉尾段供应商（matched_partner 匹配的那段），剩余拼接
    - 如「RGV-271000-淘宝电商公司」→ 去尾「淘宝电商公司」→ 「RGV-271000」
    - 如「RGV-271000-」→ 切分 [RGV, 271000] 全部 → 「RGV-271000」
    """
    import re
    parts = [p for p in re.split(r"[-_，,：:\s]+", name) if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    # 去掉尾段供应商（matched_partner.matched_part 匹配的那段）
    if matched_partner and parts[-1] == matched_partner.get("matched_part"):
        code_parts = parts[:-1]
    else:
        code_parts = parts
    return "-".join(code_parts)


async def _match_supplier_text(client: OdooClient, text: str) -> dict | None:
    """按清单「供应商」列的文本匹配 Odoo 供应商（精确匹配，忠于清单原文）。

    返回 {partner_id, name, matched_part} 或 None。

    只用 name == text 精确匹配：避免简称被 ilike 模糊替换成错误的全称
    （如清单「中研」被误配成「中研减速机TEST」）。精确无命中则返回 None，
    由调用方保留清单原文作为 supplier_name，建单时按名自动找/建供应商。
    """
    t = (text or "").strip()
    if len(t) < 2:
        return None
    try:
        rows = await client.search_read(
            "res.partner",
            ["&", ["supplier_rank", ">", 0], ["name", "=", t]],
            ["id", "name", "supplier_rank"], limit=8,
        )
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    best = max(rows, key=lambda r: (
        (r.get("supplier_rank") or 0),
        -r["id"],
    ))
    return {"partner_id": best["id"], "name": best.get("name") or "", "matched_part": t}


async def _detect_supplier_in_name(client: OdooClient, raw_name: str) -> dict | None:
    """扫描物料名称所有片段，识别其中的供应商名（自动导入，减少手动选择）。

    按 "-_:,， 空格" 切分，对每个 ≥4 字符的片段（最多 4 段）模糊匹配 Odoo supplier_rank>0 的
    res.partner。评分：完全匹配 > 包含匹配，rank 高者优先，片段长者优先。

    返回 {partner_id, name, matched_part} 或 None。片段 < 4 字符视为非供应商（避免误匹配）。
    """
    import re
    parts = [p for p in re.split(r"[-_，,：:\s]+", raw_name) if p]
    candidates = [p.strip() for p in parts if len(p.strip()) >= 4][:4]
    if not candidates:
        return None

    best: dict | None = None
    best_score = -1
    for part in candidates:
        try:
            rows = await client.search_read(
                "res.partner",
                ["&", ["supplier_rank", ">", 0], ["name", "ilike", part]],
                ["id", "name", "supplier_rank"], limit=8,
            )
        except Exception:  # noqa: BLE001
            continue
        if not rows:
            continue
        for r in rows:
            pname = r.get("name") or ""
            rank = r.get("supplier_rank") or 0
            exact = 2 if pname == part else (1 if part in pname or pname in part else 0)
            if exact == 0:
                continue
            score = exact * 100 + rank * 2 + len(part)
            if score > best_score:
                best_score = score
                best = {"partner_id": r["id"], "name": pname, "matched_part": part}
    return best


async def match_products(
    client: OdooClient,
    rows: list[dict],
    products: list[dict] | None = None,
) -> list[dict]:
    """批量识别：清单行 → 匹配产品（+ 探测供应商名）。返回每行的匹配结果。

    输入 rows: [{name, qty, spec?}]
    输出: [{name, qty, type_word, spec, matched, product_id, product_code, product_name,
            score, candidates, action, matched_partner?}]，
            action ∈ auto(自动)/choose(待选)/create(待新建)，
            matched_partner 仅在清单行尾部探测到供应商名片段时返回
    """
    if products is None:
        products = await load_product_index(client)

    results: list[dict] = []
    for row in rows:
        raw_name = str(row.get("name") or "").strip()
        qty = row.get("qty") or 0
        full_name = normalize(raw_name)
        type_word, spec = extract_type_spec(raw_name)
        hit, score, candidates = _match_candidates(type_word, spec, products, full_name=full_name)

        candidate_view = [
            {
                "product_id": c["id"],
                "product_code": c.get("default_code") or "",
                "product_name": c.get("name") or "",
                "spec": c.get("spec_info") or "",
            }
            for c in candidates[:30]
        ]

        if hit:
            action = "auto"
        elif candidates:
            action = "choose"
        else:
            action = "create"

        # 清单自带的信息（供应商列 / 备注列）
        list_supplier = (row.get("supplier") or "").strip()
        list_remark = (row.get("remark") or "").strip()

        # 供应商识别：优先清单「供应商」列，其次从名称探测
        matched_partner = None
        if list_supplier:
            matched_partner = await _match_supplier_text(client, list_supplier)
        if not matched_partner:
            matched_partner = await _detect_supplier_in_name(client, raw_name)

        # 清单自带的编号（xlsx/csv「编号」列，若有）
        list_code = (row.get("code") or "").strip()

        # 推断的编号：auto 用 Odoo 产品编码；choose/create 优先用清单自带 code，否则从名称提取
        if hit:
            inferred_code = hit.get("default_code") or ""
        else:
            inferred_code = list_code or _infer_code(raw_name, matched_partner)

        results.append({
            "name": raw_name,
            "qty": qty,
            "type_word": type_word,
            "spec": spec,
            "matched": bool(hit),
            "product_id": hit["id"] if hit else None,
            "product_code": (hit.get("default_code") or "") if hit else "",
            "product_name": (hit.get("name") or "") if hit else "",
            "score": score,
            "candidates": candidate_view,
            "action": action,
            "matched_partner": matched_partner,
            "inferred_code": inferred_code,
            "list_code": list_code,
            "list_supplier": list_supplier,
            "list_remark": list_remark,
        })
    return results


async def _template_to_product(client: OdooClient, tmpl_ids: list[int]) -> dict[int, int]:
    """product.template id → product.product id（取第一个 variant）"""
    mapping: dict[int, int] = {}
    if not tmpl_ids:
        return mapping
    variants = await client.search_read(
        "product.product", [["product_tmpl_id", "in", tmpl_ids]],
        ["id", "product_tmpl_id"], limit=len(tmpl_ids) * 2,
    )
    for v in variants:
        tmpl = v.get("product_tmpl_id")
        tid = tmpl[0] if isinstance(tmpl, (list, tuple)) and tmpl else None
        if tid and tid not in mapping:
            mapping[tid] = v["id"]
    return mapping


async def recommend_suppliers(client: OdooClient, product_ids: list[int]) -> dict[int, list[dict]]:
    """推荐供应商：supplierinfo 首选 + 历史采购频次 + supplier_rank 评分。

    product_ids 为 product.template id 列表。
    返回 {tmpl_id: [{partner_id, partner_name, price, delay, source, score}]}
    """
    tmpl_ids = [p for p in product_ids if p]
    result: dict[int, list[dict]] = {p: [] for p in tmpl_ids}
    if not tmpl_ids:
        return result

    # 1) supplierinfo
    sellers = await client.search_read(
        "product.supplierinfo",
        [["product_tmpl_id", "in", tmpl_ids], ["partner_id", "!=", False]],
        ["id", "product_tmpl_id", "partner_id", "price", "delay"],
        limit=2000,
    )
    by_tmpl: dict[int, list[dict]] = defaultdict(list)
    for s in sellers:
        tmpl = s.get("product_tmpl_id")
        tid = tmpl[0] if isinstance(tmpl, (list, tuple)) and tmpl else None
        pid = s.get("partner_id")
        pid_val = pid[0] if isinstance(pid, (list, tuple)) and pid else None
        if tid is None or pid_val is None:
            continue
        by_tmpl[tid].append({
            "partner_id": pid_val,
            "partner_name": pid[1] if isinstance(pid, (list, tuple)) and len(pid) > 1 else "",
            "price": s.get("price") or 0,
            "delay": s.get("delay") or 0,
            "source": "supplierinfo",
            "score": 0.0,
        })

    # 2) 历史采购频次（purchase.order.line → order.partner_id）
    hist: dict[int, dict[int, int]] = defaultdict(Counter)
    partner_names: dict[int, str] = {}
    if tmpl_ids:
        tmpl_to_product = await _template_to_product(client, tmpl_ids)
        product_product_ids = list(tmpl_to_product.values())
        if product_product_ids:
            pols = await client.search_read(
                MODEL_PURCHASE_LINE,
                [["product_id", "in", product_product_ids], ["state", "!=", "cancel"]],
                ["id", "product_id", "order_id"], limit=5000,
            )
            order_ids = list({o["order_id"][0] for o in pols if isinstance(o.get("order_id"), (list, tuple)) and o["order_id"]})
            order_partner: dict[int, int] = {}
            if order_ids:
                pos = await client.search_read(
                    MODEL_PURCHASE, [["id", "in", order_ids]],
                    ["id", "partner_id"], limit=len(order_ids),
                )
                order_partner = {p["id"]: (p["partner_id"][0] if isinstance(p.get("partner_id"), (list, tuple)) and p["partner_id"] else None) for p in pos}
            for l in pols:
                oid = l["order_id"][0] if isinstance(l.get("order_id"), (list, tuple)) and l["order_id"] else None
                partner_id = order_partner.get(oid)
                ppid = l["product_id"][0] if isinstance(l.get("product_id"), (list, tuple)) and l["product_id"] else None
                if partner_id is None or ppid is None:
                    continue
                tmpl_id = next((t for t, v in tmpl_to_product.items() if v == ppid), None)
                if tmpl_id is not None:
                    hist[tmpl_id][partner_id] += 1

    # 3) supplier_rank（供应商等级 = 采购历史累计）
    partner_ids = list({pid for lst in by_tmpl.values() for pid_ in lst for pid in [pid_["partner_id"]]})
    partner_ids += [pid for c in hist.values() for pid in c]
    partner_ids = list(set(partner_ids))
    rank_map: dict[int, int] = {}
    if partner_ids:
        partners = await client.search_read(
            MODEL_PARTNER, [["id", "in", partner_ids]],
            ["id", "name", "supplier_rank"], limit=len(partner_ids),
        )
        rank_map = {p["id"]: p.get("supplier_rank") or 0 for p in partners}
        partner_names.update({p["id"]: p.get("name") or "" for p in partners})

    # 组装 + 评分
    for tid in tmpl_ids:
        merged: dict[int, dict] = {}
        for s in by_tmpl.get(tid, []):
            merged[s["partner_id"]] = dict(s)
        for partner_id, freq in hist.get(tid, {}).items():
            if partner_id in merged:
                merged[partner_id]["freq"] = freq
            else:
                merged[partner_id] = {
                    "partner_id": partner_id,
                    "partner_name": partner_names.get(partner_id, ""),
                    "price": 0,
                    "delay": 0,
                    "source": "history",
                    "freq": freq,
                    "score": 0.0,
                }
        ranked = []
        for pid, rec in merged.items():
            rank = rank_map.get(pid, 0)
            freq = rec.get("freq", 0)
            source = rec.get("source", "supplierinfo")
            # 评分：supplierinfo=0.40 + 频次=0.35 + 等级=0.25
            src_score = 1.0 if source == "supplierinfo" else 0.5
            freq_score = min(freq, 10) / 10.0
            rank_score = min(rank, 10) / 10.0
            score = 0.40 * src_score + 0.35 * freq_score + 0.25 * rank_score
            rec["score"] = round(score, 3)
            rec["partner_name"] = rec["partner_name"] or partner_names.get(pid, "")
            rec["freq"] = freq
            rec["supplier_rank"] = rank
            ranked.append(rec)
        ranked.sort(key=lambda x: (-x["score"], x["delay"], x["partner_id"]))
        result[tid] = ranked[:5]
    return result


def _eta_plus_delay(delay_days: Any) -> str:
    return (date.today() + timedelta(days=max(int(delay_days or 0), 1))).isoformat()


def _norm_dt(s: str | None) -> str | None:
    """时间字段规范化：兼容 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DDTHH:MM' → Odoo Datetime 字符串。

    前端 datetime-local 控件提交的是 'YYYY-MM-DDTHH:MM'，Odoo Datetime 字段需要 'YYYY-MM-DD HH:MM:SS'。
    """
    if not s:
        return None
    s = str(s).strip().replace("T", " ").replace("/", "-")
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", s):
        s += ":00"
    return s


async def _ensure_partner(client: OdooClient, name: str) -> int | None:
    """按名称找/建供应商（res.partner），返回 partner_id 或 None。

    清单里写了供应商名但 Odoo 匹配不到时，直接按名称创建（公司 + 供应商标记），
    避免采购员再手动建供应商。
    """
    name = str(name or "").strip()
    if not name:
        return None
    existing = await client.search_read(
        "res.partner", [["name", "=", name]],
        ["id"], limit=1,
    )
    if existing:
        return existing[0]["id"]
    pid = await client.create("res.partner", {
        "name": name,
        "is_company": True,
    })
    # Odoo 18 的 supplier_rank 在 create 时会被忽略，需 write 补设（否则 rank=0 不进供应商列表）
    try:
        await client.write("res.partner", [pid], {"supplier_rank": 1})
    except Exception:  # noqa: BLE001
        pass
    return pid


async def _ensure_product(
    client: OdooClient,
    line: dict,
    products_index: list[dict],
) -> tuple[int | None, int | None]:
    """确保行对应的产品在 Odoo 存在，返回 (tmpl_id, product_product_id) 或 (None, None)。

    - 有 product_id（match 命中）→ 找 product.product id
    - 无 product_id 但有 name → 优先内存索引精确匹配；否则实时查 Odoo（防本批次新建的漏查）；
      都没找到则自动创建 product.template
    """
    tmpl_id = line.get("product_id")
    if tmpl_id:
        variants = await client.search_read(
            "product.product", [["product_tmpl_id", "=", tmpl_id]],
            ["id"], limit=1,
        )
        return (tmpl_id, variants[0]["id"]) if variants else (tmpl_id, None)

    name = str(line.get("name") or "").strip()
    if not name:
        return None, None

    # 1) 内存索引精确匹配
    for p in products_index:
        if normalize(p.get("name")) == normalize(name):
            matched_tmpl = p["id"]
            variants = await client.search_read(
                "product.product", [["product_tmpl_id", "=", matched_tmpl]],
                ["id"], limit=1,
            )
            if variants:
                return matched_tmpl, variants[0]["id"]

    # 2) Odoo 实时查（避免本批次或近批次创建后重复创建）
    existing = await client.search_read(
        "product.template", [["name", "=", name]],
        ["id"], limit=1,
    )
    if existing:
        tid = existing[0]["id"]
        variants = await client.search_read(
            "product.product", [["product_tmpl_id", "=", tid]],
            ["id"], limit=1,
        )
        if variants:
            return tid, variants[0]["id"]

    # 3) 找不到 → 自动创建产品主数据（purchase_ok=True、可存储商品）
    new_tmpl_id = await client.create("product.template", {
        "name": name,
        "purchase_ok": True,
        "active": True,
        "type": "consu",
    })
    variants = await client.search_read(
        "product.product", [["product_tmpl_id", "=", new_tmpl_id]],
        ["id"], limit=1,
    )
    return (new_tmpl_id, variants[0]["id"]) if variants else (new_tmpl_id, None)


PLACEHOLDER_PRODUCT_NAME = "临时外购件（清单导入）"


async def _get_or_create_placeholder(client: OdooClient) -> tuple[int, int] | None:
    """找/建「临时外购件」占位产品，返回 (tmpl_id, product_product_id)。

    不建料模式：无对应产品的行统一挂到占位产品，采购行描述写实际物料名。
    """
    existing = await client.search_read(
        "product.template", [["name", "=", PLACEHOLDER_PRODUCT_NAME]],
        ["id"], limit=1,
    )
    if existing:
        tid = existing[0]["id"]
    else:
        tid = await client.create("product.template", {
            "name": PLACEHOLDER_PRODUCT_NAME,
            "purchase_ok": True,
            "active": True,
            "type": "consu",
        })
    variants = await client.search_read(
        "product.product", [["product_tmpl_id", "=", tid]],
        ["id"], limit=1,
    )
    return (tid, variants[0]["id"]) if variants else None


async def batch_create_purchase_orders(
    client: OdooClient,
    lines: list[dict],
    auto_create_product: bool = False,
    urgent: bool = True,
    purchase_date: str | None = None,
    delivery_date: str | None = None,
    list_name: str | None = None,
) -> dict[str, Any]:
    """按供应商聚合批量建采购单（date_planned + 回写 supplierinfo）。

    lines: [{product_id?, name?, qty, partner_id?, price?, delay?, spec?, code?}]
    - urgent=True（默认）→ priority=1 紧急（进紧急采购看板）；urgent=False → 普通采购单
    - purchase_date（可选）：订单日期，写入 purchase.order.date_order（缺省 Odoo 当前时间）
    - delivery_date（可选）：计划到货日期，写入订单行 date_planned（缺省按供应商交期 今天+delay）
    - list_name（可选）：清单名，写入 purchase.order.origin（前缀「清单:」），便于按清单聚合/搜索
    - product_id 命中（match 已识别）→ 直接用现有产品
    - 无 product_id：
      · auto_create_product=True  → 按 name 自动创建产品主数据
      · auto_create_product=False（默认）→ 不建料，挂到「临时外购件」占位产品，
        采购行描述（name 字段）写实际物料名称，采购单直接进入后续环节
    - partner_id 缺省时取该产品推荐供应商 Top1（不建料模式无推荐则需前端指定）
    - code（可选）：清单「编号」列，会拼到采购行 name（name + code），保证与清单内容一致
    """
    if not lines:
        return {"created": [], "skipped": [], "note": "无采购行"}

    # 时间字段规范化（前端 datetime-local 提交 'YYYY-MM-DDTHH:MM'）
    purchase_date = _norm_dt(purchase_date)
    delivery_date = _norm_dt(delivery_date)

    products_index = await load_product_index(client)
    placeholder: tuple[int, int] | None = None
    if not auto_create_product:
        placeholder = await _get_or_create_placeholder(client)

    # 1. 解析所有行：确定 tmpl_id + product_product_id + 采购行描述
    resolved: list[dict] = []
    skipped: list[dict] = []
    for l in lines:
        display_name = str(l.get("name") or "").strip()
        code = str(l.get("code") or "").strip()
        # 兜底拼接：清单「编号 + 名称」写入采购行 name；code 已在 display_name 中则不再拼
        if code and code not in display_name:
            display_name = f"{display_name} {code}".strip()
        if l.get("product_id"):
            # 已有产品（match 命中）
            tmpl_id, product_product_id = await _ensure_product(client, l, products_index)
            if not product_product_id:
                skipped.append({"name": display_name, "reason": "产品查找失败"})
                continue
            resolved.append({
                **l, "tmpl_id": tmpl_id, "product_product_id": product_product_id,
                "display_name": display_name or None, "placeholder": False,
            })
        elif auto_create_product:
            # 自动建料模式
            tmpl_id, product_product_id = await _ensure_product(client, l, products_index)
            if not product_product_id:
                skipped.append({"name": display_name, "reason": "产品创建失败"})
                continue
            resolved.append({
                **l, "tmpl_id": tmpl_id, "product_product_id": product_product_id,
                "display_name": display_name or None, "placeholder": False,
            })
        elif placeholder:
            # 不建料模式：占位产品 + 行描述
            tmpl_id, product_product_id = placeholder
            if not display_name:
                skipped.append({"name": "", "reason": "缺物料名称"})
                continue
            resolved.append({
                **l, "tmpl_id": tmpl_id, "product_product_id": product_product_id,
                "display_name": display_name, "placeholder": True,
            })
        else:
            skipped.append({"name": display_name, "reason": "占位产品不可用"})

    # 2. supplier_name → partner_id（清单明确写了供应商名但 Odoo 未匹配，直接按名称找/建）
    for l in resolved:
        if not l.get("partner_id") and l.get("supplier_name"):
            pid = await _ensure_partner(client, l["supplier_name"])
            if pid:
                l["partner_id"] = pid
            else:
                skipped.append({"name": l.get("display_name"), "reason": f"供应商创建失败：{l['supplier_name']}"})

    # 3. 缺省供应商补全：供应商以清单为准，清单无供应商 → 兜底「淘宝电商公司」
    fallback_pid: int | None = None
    for l in resolved:
        if l.get("partner_id"):
            continue
        if fallback_pid is None:
            fallback_pid = await _ensure_fallback_partner(client)
        if fallback_pid:
            l["partner_id"] = fallback_pid
        else:
            skipped.append({"name": l.get("display_name"), "reason": "无供应商（兜底供应商不可用）"})

    # 3. 按供应商聚合（带 partner_id 的走建单流程）
    by_partner: dict[int, list[dict]] = defaultdict(list)
    for l in resolved:
        if l.get("partner_id"):
            by_partner[l["partner_id"]].append(l)

    # 4. 建 PO
    created: list[dict] = []
    writebacks: list[dict] = []
    for partner_id, group in by_partner.items():
        order_lines = []
        for l in group:
            line_vals = {
                "product_id": l["product_product_id"],
                "product_qty": l.get("qty") or 1,
                "price_unit": l.get("price") or 0,
                # 交货日期：接口指定 > 行内 date_planned > 供应商交期（今天+delay）
                "date_planned": delivery_date or l.get("date_planned") or _eta_plus_delay(l.get("delay", 0)),
            }
            # 采购行描述：占位产品或已建料时写明实际物料名称
            if l.get("display_name"):
                line_vals["name"] = l["display_name"]
            # 采购行备注：清单备注写入 note 字段（衔接采购看板展示）
            if l.get("remark"):
                line_vals["note"] = l["remark"]
            order_lines.append((0, 0, line_vals))
        po_vals = {
            "partner_id": partner_id,
            "priority": PRIORITY_URGENT if urgent else PRIORITY_NORMAL,  # 紧急标记（衔接看板）
            "origin": f"清单:{list_name}" if list_name else "清单导入",
            "order_line": order_lines,
        }
        # 采购日期：接口指定则写入 date_order（缺省 Odoo 当前时间）
        if purchase_date:
            po_vals["date_order"] = purchase_date
        try:
            po_id = await client.create(MODEL_PURCHASE, po_vals)
            po = await client.search_read(MODEL_PURCHASE, [["id", "=", po_id]], ["name", "state"], limit=1)
            po_name = po[0]["name"] if po else f"PO{po_id}"
            created.append({
                "po_id": po_id,
                "po_name": po_name,
                "partner_id": partner_id,
                "state": po[0]["state"] if po else "draft",
                "line_count": len(group),
                "lines": [
                    {"product_id": l["tmpl_id"], "name": l.get("display_name"), "qty": l.get("qty") or 1}
                    for l in group
                ],
            })
            # supplierinfo 回写：仅真实产品（非占位）
            writebacks.extend([
                {"product_tmpl_id": l["tmpl_id"], "partner_id": partner_id,
                 "price": l.get("price") or 0, "delay": l.get("delay") or 0}
                for l in group if not l["placeholder"]
            ])
        except Exception as e:  # noqa: BLE001
            logger.exception("批量建 PO 失败（供应商 %s）", partner_id)
            skipped.append({"partner_id": partner_id, "reason": str(e)[:120]})

    # 5. 回写 supplierinfo（仅真实产品）
    wrote = await writeback_supplierinfo(client, writebacks)

    return {
        "created": created,
        "skipped": skipped,
        "writeback": {"count": wrote},
        "note": f"已生成 {len(created)} 张采购单，回写供应商 {wrote} 条，跳过 {len(skipped)} 行",
    }


async def writeback_supplierinfo(client: OdooClient, entries: list[dict]) -> int:
    """回写 product.supplierinfo：已有同 (tmpl, partner) 记录则更新价格/交期，否则新建。

    entries: [{product_tmpl_id, partner_id, price, delay}]
    返回写入条数。
    """
    if not entries:
        return 0
    written = 0
    # 去重
    seen: set[tuple[int, int]] = set()
    uniq: list[dict] = []
    for e in entries:
        key = (e["product_tmpl_id"], e["partner_id"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)

    tmpl_ids = list({e["product_tmpl_id"] for e in uniq})
    existing = await client.search_read(
        "product.supplierinfo",
        [["product_tmpl_id", "in", tmpl_ids]],
        ["id", "product_tmpl_id", "partner_id"], limit=5000,
    )
    exist_key_to_id: dict[tuple[int, int], int] = {}
    for s in existing:
        tmpl = s.get("product_tmpl_id")
        partner = s.get("partner_id")
        tid = tmpl[0] if isinstance(tmpl, (list, tuple)) and tmpl else None
        pid = partner[0] if isinstance(partner, (list, tuple)) and partner else None
        if tid is not None and pid is not None:
            exist_key_to_id[(tid, pid)] = s["id"]

    for e in uniq:
        key = (e["product_tmpl_id"], e["partner_id"])
        try:
            if key in exist_key_to_id:
                await client.write("product.supplierinfo", [exist_key_to_id[key]], {
                    "price": e.get("price") or 0,
                    "delay": e.get("delay") or 0,
                })
            else:
                await client.create("product.supplierinfo", {
                    "product_tmpl_id": e["product_tmpl_id"],
                    "partner_id": e["partner_id"],
                    "price": e.get("price") or 0,
                    "delay": e.get("delay") or 0,
                })
            written += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("回写 supplierinfo 失败 tmpl=%s partner=%s: %s",
                           e["product_tmpl_id"], e["partner_id"], exc)
    return written
