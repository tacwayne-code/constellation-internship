"""清单导入数量/表头解析单元测试（纯函数，无需 Odoo）"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.routers.list_import import ParseRequest, _norm_header, _parse_qty, parse_list


def _approx(a, b):
    return a is not None and b is not None and abs(a - b) < 1e-9


def test_parse_qty_basic():
    assert _parse_qty(48) == 48.0
    assert _parse_qty("48") == 48.0
    assert _parse_qty("48.5") == 48.5
    assert _parse_qty("0") == 0.0


def test_parse_qty_sum():
    assert _parse_qty("48+3") == 51.0
    assert _parse_qty("48 + 3") == 51.0
    assert _parse_qty("48＋3") == 51.0  # 全角加号
    assert _parse_qty("48+3件") == 51.0


def test_parse_qty_units():
    assert _parse_qty("48个") == 48.0
    assert _parse_qty("48 件") == 48.0
    assert _parse_qty("2台套") == 2.0
    assert _parse_qty("3.5pcs") == 3.5
    assert _parse_qty("6 sets") == 6.0


def test_parse_qty_non_numeric():
    assert _parse_qty("") is None
    assert _parse_qty(None) is None
    assert _parse_qty("天轨用") is None      # 分类行
    assert _parse_qty("16×3") is None        # 规格，不是数量


def test_norm_header():
    assert _norm_header("数量（个）") == "数量"
    assert _norm_header("需求数量 (件)") == "需求数量"
    assert _norm_header("名称/规格") == "名称/规格"
    assert _norm_header("数量") == "数量"


def test_parse_list_text():
    rows = asyncio.run(parse_list(ParseRequest(text="平垫圈16×3,48\n六角螺母 M16 48个\nM12X100化学螺栓,24\n天轨用")))
    assert rows["count"] == 4
    r = {x["name"]: x["qty"] for x in rows["rows"]}
    assert _approx(r.get("平垫圈16×3"), 48.0)
    assert _approx(r.get("六角螺母 M16"), 48.0)   # 单位「个」被剥离，数量正确
    assert _approx(r.get("M12X100化学螺栓"), 24.0)
    assert _approx(r.get("天轨用"), 1.0)          # 无数量 → 默认 1


if __name__ == "__main__":
    test_parse_qty_basic()
    test_parse_qty_sum()
    test_parse_qty_units()
    test_parse_qty_non_numeric()
    test_norm_header()
    test_parse_list_text()
    print("ALL PARSING TESTS PASSED")
