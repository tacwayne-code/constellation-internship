"""清单导入匹配逻辑单元测试（纯函数，无需连接 Odoo）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.import_matching import _match_candidates, extract_type_spec, normalize


def _products(items):
    return [
        {"id": i + 1, "name": n, "default_code": c, "spec_info": s}
        for i, (n, c, s) in enumerate(items)
    ]


def _ids(candidates):
    return [c["id"] for c in candidates]


def test_union_keeps_spec_embedded_name():
    """存在「名称恰为类型词」的通用件时，名称内嵌规格的型号不能被漏掉。"""
    products = _products([
        ("平垫圈", "P01000", ""),          # 通用件（名称恰为类型词）
        ("平垫圈16×3", "P02658", ""),      # 型号内嵌在名称里
        ("平垫圈8×2.1", "P02659", ""),
    ])
    type_word, spec = extract_type_spec("平垫圈16×3")
    assert type_word == "平垫圈" and spec == "16*3"

    hit, score, candidates = _match_candidates(type_word, spec, products, full_name=normalize("平垫圈16×3"))
    ids = _ids(candidates)
    # 核心回归：所需型号必须出现在候选里（修复前会被「精确相等」分支整批丢弃）
    assert 2 in ids, f"所需型号「平垫圈16×3」应出现在候选里，实际 ids={ids}"
    # 名称与整行输入完全一致 → 直接自动命中
    assert hit is not None and hit["id"] == 2, f"应自动命中 id=2，实际 hit={hit}"
    assert score == 100


def test_spec_info_exact_auto_hit():
    """spec_info 精确命中仍应自动命中（回归保护）。"""
    products = _products([
        ("平垫圈", "P02658", "16*3"),
    ])
    type_word, spec = extract_type_spec("平垫圈16×3")
    hit, score, candidates = _match_candidates(type_word, spec, products, full_name=normalize("平垫圈16×3"))
    assert hit is not None and hit["id"] == 1
    assert score == 100


def test_choose_orders_by_spec_similarity():
    """多候选且无精确命中时，规格最接近的型号排第一（choose 分支排序）。"""
    products = _products([
        ("平垫圈", "P01000", ""),          # 通用件，无规格
        ("平垫圈", "P02659", "8*2.1"),     # 规格无关
        ("平垫圈", "P02658", "16*30"),     # 16*3 的前缀匹配 → 最接近
    ])
    type_word, spec = extract_type_spec("平垫圈16×3")
    assert spec == "16*3"
    hit, score, candidates = _match_candidates(type_word, spec, products, full_name=normalize("平垫圈16×3"))
    assert hit is None and score == 70, "无精确命中应为待选择（choose）"
    assert _ids(candidates)[0] == 3, f"规格最接近的型号应排第一，实际={_ids(candidates)}"


def test_code_fallback():
    """名称维度匹配不到时，按编号兜底，支持「型号=编码」清单。"""
    products = _products([
        ("堆垛机立柱铸件", "Z-271000", ""),
    ])
    type_word, spec = extract_type_spec("Z-271000")
    hit, score, candidates = _match_candidates(type_word, spec, products, full_name=normalize("Z-271000"))
    ids = _ids(candidates)
    assert 1 in ids, f"按编码应能兜底匹配到产品，实际候选 ids={ids}"


def test_no_reverse_substring_false_positive():
    """通用名产品（如「配件」）不应被反向子串误匹配到含该词的输入。"""
    products = _products([
        ("配件", "P04644", "6ES7590-1AB60-0AA0"),
    ])
    type_word, spec = extract_type_spec("特殊配件XYZ")
    hit, score, candidates = _match_candidates(type_word, spec, products, full_name=normalize("特殊配件XYZ"))
    assert hit is None, "「特殊配件XYZ」不应精确命中通用产品「配件」"
    assert candidates == [], "「配件」不应出现在「特殊配件XYZ」的候选里"


if __name__ == "__main__":
    test_union_keeps_spec_embedded_name()
    test_spec_info_exact_auto_hit()
    test_choose_orders_by_spec_similarity()
    test_code_fallback()
    test_no_reverse_substring_false_positive()
    print("ALL TESTS PASSED")
