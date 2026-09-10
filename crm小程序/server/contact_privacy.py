"""Personal contacts are keyed only by the authenticated CRM actor.

Legacy contacts remain untouched on disk and are never adopted automatically.
The same projection applies to managers, collections and mutation responses.
"""
from __future__ import annotations

import copy
import uuid


PRIVATE_KEYS = {
    "_salesContacts", "personalContact", "contacts", "contact", "phone",
    "contactName", "contactPhone", "mobile", "email", "telephone",
    "requestPayload", "responsePayload",
}


def scrub(value):
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items() if key not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return copy.deepcopy(value)


def contact_input(body, collection):
    """Validate before touching state; never accept caller-selected ownership."""
    if "_salesContacts" in body:
        raise ValueError("不能指定或修改其他人员的联系人")
    raw = body.get("personalContact")
    if raw is None and collection == "customers":
        contacts = body.get("contacts") or []
        if not isinstance(contacts, list) or any(not isinstance(c, dict) for c in contacts):
            raise ValueError("联系人格式不正确")
        raw = next((c for c in contacts if c.get("isPrimary")), contacts[0] if contacts else None)
        if raw is None and (body.get("contact") or body.get("phone")):
            raw = {"name": body.get("contact"), "phone": body.get("phone")}
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("联系人格式不正确")
    if any(key in raw for key in ("ownerId", "actorId", "createdBy", "_salesContacts")):
        raise ValueError("联系人归属由登录账号确定")
    name, phone = raw.get("name") or "", raw.get("phone") or ""
    if not isinstance(name, str) or not isinstance(phone, str):
        raise ValueError("联系人姓名和电话必须是文字")
    name, phone = name.strip(), phone.strip()
    if not name and not phone:
        return None
    if not name or len(name) > 100 or len(phone) > 50:
        raise ValueError("请填写有效的联系人姓名和电话")
    return {"name": name, "phone": phone}


def save_contact(customer, actor, contact, now):
    if not contact:
        return
    # Copy-on-write also preserves all other employees' contacts.
    mapping = copy.deepcopy(customer.get("_salesContacts") or {})
    owned = mapping.setdefault(actor["id"], [])
    match = next((c for c in owned if c["name"] == contact["name"] and c["phone"] == contact["phone"]), None)
    if match and match.get("isPrimary"):
        return False
    for item in owned:
        item["isPrimary"] = False
    if match is None:
        match = {"id": f"CONTACT-{uuid.uuid4()}", **contact, "createdAt": now}
        owned.append(match)
    match.update(isPrimary=True, updatedAt=now)
    customer["_salesContacts"] = mapping
    return True


def project(item, collection, actor, customers):
    result = scrub(item)
    if collection == "customers":
        customer = item
    elif collection in {"visits", "opportunities", "sales"}:
        customer = next((c for c in customers if c.get("id") == item.get("customerId")), {})
    else:
        return result
    owned = customer.get("_salesContacts", {}).get(actor["id"], [])
    # Do not return storage metadata or any other actor's bucket.
    contacts = [{key: c.get(key, "") for key in ("id", "name", "phone", "isPrimary")} for c in owned]
    primary = next((c for c in contacts if c.get("isPrimary")), None)
    result["personalContact"] = {"name": primary["name"], "phone": primary["phone"]} if primary else None
    if collection == "customers":
        result["contacts"] = contacts
        result["contact"] = primary["name"] if primary else ""
        result["phone"] = primary["phone"] if primary else ""
    return result
