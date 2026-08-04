{
    "name": "MRP Consume Order Fix",
    "summary": "Shared remaining/total quantity template for manufacturing views",
    "version": "18.0.1.0.0",
    "category": "Manufacturing",
    "license": "LGPL-3",
    "depends": ["mrp", "web"],
    "assets": {
        "web.assets_backend": [
            "mrp_consume_order_fix/static/src/xml/remaining_quantity.xml",
        ],
    },
    "installable": True,
    "application": False,
}
