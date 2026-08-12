"""Install or verify remaining-quantity display fixes in Odoo.

The manufacturing form normally shows ``qty_producing / product_qty``.
``qty_producing`` drives Odoo's component inverse calculation, so changing it
only to improve the label also changes component consumption.  This fix keeps
that technical field intact and displays separate computed values for the MO
header and its component lines instead.
"""

from __future__ import annotations

import argparse
import math
import sys


FIELD_NAME = "x_worker_report_qty_remaining"
COMPONENT_FIELD_NAME = "x_worker_report_remaining_consumption"
VIEW_NAME = "worker.report.mrp.production.remaining.quantity.form"
VIEW_KEY = "worker_report.mrp_production_remaining_quantity_form"
FIELD_COMPUTE = """for record in self:
    record['x_worker_report_qty_remaining'] = max(
        (record.product_qty or 0.0) - (record.qty_produced or 0.0),
        0.0,
    )
"""
COMPONENT_FIELD_COMPUTE = """for record in self:
    planned = record.product_uom_qty or 0.0
    consumed = (record.quantity or 0.0) if record.picked else 0.0
    record['x_worker_report_remaining_consumption'] = max(
        planned - consumed,
        0.0,
    )
"""
VIEW_ARCH = """
<data>
    <xpath expr="//field[@name='qty_producing' and contains(@class, 'text-start')]" position="attributes">
        <attribute name="invisible">1</attribute>
    </xpath>
    <xpath expr="//field[@name='qty_producing' and contains(@class, 'text-start')]" position="after">
        <field name="x_worker_report_qty_remaining"
               class="text-start text-truncate"
               readonly="1"/>
    </xpath>
    <xpath expr="//field[@name='move_raw_ids']/list/field[@name='product_uom_qty' and @widget='mrp_should_consume']" position="attributes">
        <attribute name="column_invisible">True</attribute>
    </xpath>
    <xpath expr="//field[@name='move_raw_ids']/list/field[@name='product_uom_qty' and not(@widget)]" position="attributes">
        <attribute name="column_invisible">True</attribute>
    </xpath>
    <xpath expr="//field[@name='move_raw_ids']/list/field[@name='product_uom_qty' and @widget='mrp_should_consume']" position="after">
        <field name="x_worker_report_remaining_consumption"
               string="待消耗"
               readonly="1"/>
    </xpath>
</data>
""".strip()


def validate_view_arch(arch=VIEW_ARCH):
    """Reject a view definition that leaves Odoo's inverse column visible."""
    from xml.etree import ElementTree

    root = ElementTree.fromstring(arch)
    original_column_xpath = (
        "//field[@name='move_raw_ids']/list/field"
        "[@name='product_uom_qty' and @widget='mrp_should_consume']"
    )
    matching_xpaths = [
        node for node in root.findall("xpath")
        if node.get("expr") == original_column_xpath
    ]
    hides_original = any(
        attribute.get("name") == "column_invisible"
        and (attribute.text or "").strip().lower() in {"1", "true"}
        for node in matching_xpaths
        for attribute in node.findall("attribute")
    )
    adds_replacement = any(
        node.get("position") == "after"
        and any(
            field.get("name") == COMPONENT_FIELD_NAME
            for field in node.findall("field")
        )
        for node in matching_xpaths
    )
    plain_column_xpath = (
        "//field[@name='move_raw_ids']/list/field"
        "[@name='product_uom_qty' and not(@widget)]"
    )
    hides_plain = any(
        node.get("expr") == plain_column_xpath
        and any(
            attribute.get("name") == "column_invisible"
            and (attribute.text or "").strip().lower() in {"1", "true"}
            for attribute in node.findall("attribute")
        )
        for node in root.findall("xpath")
    )
    if not hides_original or not hides_plain or not adds_replacement:
        raise ValueError(
            "The Odoo consumption columns must be hidden and replaced"
        )


class OdooRemainingQuantityFix:
    """Normalize Odoo quantities without writing to Odoo.

    The service must not use ``qty_producing`` as a display-only field because
    Odoo also uses it for component inverse calculations.  These helpers keep
    the display/consumption calculations derived from the source quantities
    and are safe to use with both the real and fake clients.
    """

    @staticmethod
    def _number(value):
        try:
            value = float(value or 0)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    @classmethod
    def apply_to_workorder_fix(cls, workorder, client=None):
        """Populate a workorder's remaining quantity from planned/produced.

        ``client`` is accepted for compatibility with callers that pass an
        Odoo client, but no remote write is performed by this method.
        """
        if not isinstance(workorder, dict):
            return workorder
        planned = cls._number(workorder.get("qty_production"))
        produced = cls._number(workorder.get("qty_produced"))
        if planned is None or produced is None:
            return workorder
        workorder["qty_remaining"] = max(planned - produced, 0.0)
        return workorder

    @classmethod
    def remaining_consumption_qty(cls, move):
        """Return Odoo's technical remaining-consumption quantity.

        This helper is used by the report synchronization path when it derives
        the cumulative consumed amount.  Keep Odoo's value when valid and only
        fall back to a calculation for older or incomplete responses.
        """
        if not isinstance(move, dict):
            return 0.0
        planned = cls._number(move.get("product_uom_qty"))
        if planned is None:
            return 0.0
        planned = max(planned, 0.0)
        candidate = cls._number(move.get("should_consume_qty"))
        if candidate is not None and 0.0 <= candidate <= planned:
            return candidate
        consumed = cls._number(move.get("quantity")) or 0.0
        return max(planned - max(consumed, 0.0), 0.0)

    @classmethod
    def display_remaining_consumption_qty(cls, move):
        """Return planned quantity minus confirmed component consumption.

        Odoo can put reserved quantities in ``quantity`` before consumption.
        The integration marks a move as ``picked`` when that quantity becomes
        actual consumption, so unpicked reservations must not reduce the
        displayed remaining amount.
        """
        if not isinstance(move, dict):
            return 0.0
        planned = cls._number(move.get("product_uom_qty"))
        if planned is None:
            return 0.0
        planned = max(planned, 0.0)
        consumed = (cls._number(move.get("quantity")) or 0.0) if move.get("picked") else 0.0
        return max(planned - max(consumed, 0.0), 0.0)


def _client():
    # Import lazily so server.py can reuse the pure helpers without a circular
    # import during module initialization.
    import server
    return server.get_odoo()


def _model_id(client, model: str) -> int:
    rows = client.search_read(
        "ir.model", [["model", "=", model]], ["id"], limit=1
    )
    if not rows:
        raise RuntimeError(f"Odoo model {model} was not found")
    return int(rows[0]["id"])


def _base_view_id(client) -> int:
    rows = client.search_read(
        "ir.ui.view",
        [["model", "=", "mrp.production"], ["name", "=", "mrp.production.form"], ["inherit_id", "=", False]],
        ["id"],
        limit=1,
    )
    if not rows:
        raise RuntimeError("The base mrp.production form view was not found")
    return int(rows[0]["id"])


def install() -> None:
    validate_view_arch()
    client = _client()
    model_id = _model_id(client, "mrp.production")
    field_rows = client.search_read(
        "ir.model.fields",
        [["model_id", "=", model_id], ["name", "=", FIELD_NAME]],
        ["id"],
        limit=1,
    )
    field_values = {
        "name": FIELD_NAME,
        "field_description": "Remaining Quantity",
        "model_id": model_id,
        "model": "mrp.production",
        "ttype": "float",
        "state": "manual",
        "readonly": True,
        "store": False,
        "depends": "product_qty,qty_produced",
        "compute": FIELD_COMPUTE,
    }
    if field_rows:
        client.call("ir.model.fields", "write", [[field_rows[0]["id"]], field_values])
    else:
        client.call("ir.model.fields", "create", [field_values])

    component_model_id = _model_id(client, "stock.move")
    component_field_rows = client.search_read(
        "ir.model.fields",
        [["model_id", "=", component_model_id], ["name", "=", COMPONENT_FIELD_NAME]],
        ["id"],
        limit=1,
    )
    component_field_values = {
        "name": COMPONENT_FIELD_NAME,
        "field_description": "Remaining Consumption",
        "model_id": component_model_id,
        "model": "stock.move",
        "ttype": "float",
        "state": "manual",
        "readonly": True,
        "store": False,
        "depends": "product_uom_qty,quantity,picked",
        "compute": COMPONENT_FIELD_COMPUTE,
    }
    if component_field_rows:
        client.call(
            "ir.model.fields", "write",
            [[component_field_rows[0]["id"]], component_field_values],
        )
    else:
        client.call("ir.model.fields", "create", [component_field_values])

    base_view_id = _base_view_id(client)
    view_rows = client.search_read(
        "ir.ui.view",
        [["model", "=", "mrp.production"], ["name", "=", VIEW_NAME]],
        ["id"],
        limit=1,
    )
    view_values = {
        "name": VIEW_NAME,
        "key": VIEW_KEY,
        "model": "mrp.production",
        "inherit_id": base_view_id,
        "priority": 90,
        "mode": "extension",
        "active": True,
        "arch_db": VIEW_ARCH,
    }
    if view_rows:
        client.call("ir.ui.view", "write", [[view_rows[0]["id"]], view_values])
    else:
        client.call("ir.ui.view", "create", [view_values])

    verify()


def verify() -> None:
    validate_view_arch()
    client = _client()
    rows = client.search_read(
        "mrp.production",
        [["id", "=", 49]],
        ["name", "product_qty", "qty_produced", "qty_producing", FIELD_NAME, "move_raw_ids"],
        limit=1,
    )
    if not rows:
        raise RuntimeError("Manufacturing order #49 was not found")
    row = rows[0]
    expected = max(float(row["product_qty"] or 0) - float(row["qty_produced"] or 0), 0.0)
    actual = float(row[FIELD_NAME] or 0)
    if abs(actual - expected) > 1e-6:
        raise RuntimeError(f"Remaining quantity is {actual}, expected {expected}")
    moves = client.call(
        "stock.move", "read",
        [row["move_raw_ids"], [
            "product_uom_qty", "quantity", "picked", "should_consume_qty",
            COMPONENT_FIELD_NAME,
        ]],
    )
    if not moves:
        raise RuntimeError(f"{row['name']} has no component moves")
    for move in moves:
        expected_move = OdooRemainingQuantityFix.display_remaining_consumption_qty(move)
        actual_move = float(move[COMPONENT_FIELD_NAME] or 0)
        if abs(actual_move - expected_move) > 1e-6:
            raise RuntimeError(
                f"Move #{move['id']} remaining consumption is {actual_move}, "
                f"expected {expected_move}"
            )
    print(
        f"{row['name']}: display={actual:g}/{float(row['product_qty']):g}, "
        f"qty_producing={float(row['qty_producing']):g} (preserved), "
        f"components={len(moves)}, remaining={float(moves[0][COMPONENT_FIELD_NAME]):g}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("install", "verify"), nargs="?", default="verify")
    args = parser.parse_args()
    try:
        install() if args.command == "install" else verify()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
