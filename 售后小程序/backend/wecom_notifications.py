"""Transactional assignment outbox. No customer details or credentials in logs."""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta

from database import SessionLocal
from models import Engineer, Notification, WorkOrder, User


def enabled():
    return os.getenv("WECOM_NOTIFICATIONS_ENABLED", "0") == "1"


def bindings():
    # Explicit ASS user primary-key mapping; never infer an identity from a name.
    value = json.loads(os.getenv("WECOM_USER_BINDINGS", "{}"))
    if not isinstance(value, dict) or any(
        not str(k).isdigit() or not isinstance(v, str)
        or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", v) or v == "@all"
        for k, v in value.items()
    ):
        raise ValueError("INVALID_BINDINGS")
    if len({v.lower() for v in value.values()}) != len(value):
        raise ValueError("DUPLICATE_BINDING")
    return value


def enqueue_assignment(db, order, actor_id):
    if not order.engineer_id:
        return
    db.flush()
    # Also invalidate A→B→A old events: matching only the current engineer is insufficient.
    db.query(Notification).filter(
        Notification.work_order_id == order.id,
        Notification.event_type == "ASSIGNMENT",
        Notification.status.in_(["PENDING", "RETRY", "FAILED", "BLOCKED"]),
    ).update({"status": "CANCELLED", "error_code": "SUPERSEDED", "updated_at": datetime.utcnow()})
    event_id = uuid.uuid4().hex
    db.add(Notification(
        id=event_id, work_order_id=order.id, engineer_id=order.engineer_id,
        actor_id=actor_id, status="PENDING" if enabled() else "SKIPPED",
        content=f"售后派单通知\n工单：{order.order_no}\n请打开企业服务小程序 → 售后服务 → 我的任务，查看最新指派。\n通知编号：{event_id}",
    ))


def enqueue_service_request(db, order):
    db.flush()
    configured = os.getenv("WECOM_DISPATCHER_USER_ID", "").strip()
    target_id = int(configured) if configured.isdigit() and int(configured) > 0 else None
    # Deterministic event id: the same saved application can only notify once.
    event_id = "request-" + str(order.id)
    if db.get(Notification, event_id):
        return
    db.add(Notification(id=event_id, event_type="SERVICE_REQUEST", target_user_id=target_id,
        work_order_id=order.id, engineer_id=0, actor_id=0,
        status="PENDING" if enabled() else "SKIPPED",
        content=f"新售后申请，等待派单\n工单：{order.order_no}\n请打开企业服务小程序 → 售后服务 → 工单 → 待派单，查看申请并安排工程师。\n通知编号：{event_id}"))


class SendError(Exception):
    def __init__(self, code, retry=False, uncertain=False):
        self.code, self.retry, self.uncertain = code, retry, uncertain


class WeComClient:
    def __init__(self):
        self.token, self.expires = "", 0

    def request(self, path, query, payload=None):
        url = "https://qyapi.weixin.qq.com/cgi-bin/" + path + "?" + urllib.parse.urlencode(query)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read(1024 * 1024))
        except Exception:
            # Exception text/URLs can contain credentials; never persist them.
            raise SendError("TRANSPORT", retry=payload is None, uncertain=payload is not None) from None

    def access_token(self):
        if self.token and time.monotonic() < self.expires:
            return self.token
        corp = os.getenv("WECOM_CORP_ID", "").strip()
        secret = os.getenv("WECOM_APP_SECRET", "").strip()
        if not corp or not secret or not os.getenv("WECOM_AGENT_ID", "").isdigit():
            raise SendError("NOT_CONFIGURED")
        result = self.request("gettoken", {"corpid": corp, "corpsecret": secret})
        if result.get("errcode", 0) or not result.get("access_token"):
            raise SendError("TOKEN_REJECTED")
        self.token = result["access_token"]
        self.expires = time.monotonic() + max(0, int(result.get("expires_in", 7200)) - 120)
        return self.token

    def send(self, recipient, content):
        for _ in range(2):
            token = self.access_token()
            result = self.request("message/send", {"access_token": token}, {
                "touser": recipient, "agentid": int(os.environ["WECOM_AGENT_ID"]),
                "msgtype": "text", "text": {"content": content},
                "enable_duplicate_check": 1, "duplicate_check_interval": 1800,
            })
            code = result.get("errcode", -1)
            if code in (40014, 42001):
                self.token = ""
                continue
            if code:
                raise SendError(f"API_{int(code)}", retry=code in (-1, 45009))
            if any(result.get(k) for k in ("invaliduser", "invalidparty", "invalidtag", "unlicenseduser")):
                raise SendError("RECIPIENT_REJECTED")
            return result.get("msgid", "")
        raise SendError("TOKEN_EXPIRED", retry=True)


def process_one(client, session_factory=SessionLocal):
    if not enabled():
        return False
    now = datetime.utcnow()
    with session_factory() as db:
        # A crashed process may have sent a message. Do not blindly send it again.
        db.query(Notification).filter(
            Notification.status == "SENDING", Notification.updated_at < now - timedelta(minutes=5)
        ).update({"status": "UNKNOWN", "error_code": "INTERRUPTED", "updated_at": now})
        db.commit()
        row = db.query(Notification).filter(
            Notification.status.in_(["PENDING", "RETRY"]), Notification.next_attempt_at <= now
        ).order_by(Notification.created_at).first()
        if not row:
            return False
        event_id = row.id
        claimed = db.query(Notification).filter(
            Notification.id == event_id, Notification.status.in_(["PENDING", "RETRY"])
        ).update({"status": "SENDING", "updated_at": now, "attempts": Notification.attempts + 1})
        db.commit()
        if not claimed:
            return True
        db.expire_all()
        row = db.get(Notification, event_id)
        order = db.get(WorkOrder, row.work_order_id)
        engineer = db.get(Engineer, row.engineer_id)
        try:
            if row.event_type == "SERVICE_REQUEST":
                target = db.get(User, row.target_user_id) if row.target_user_id else None
                if not order or order.status != "pending" or order.engineer_id:
                    row.status, row.error_code = "CANCELLED", "ALREADY_DISPATCHED"
                elif not target or target.role != "paidan":
                    row.status, row.error_code = "BLOCKED", "DISPATCHER_UNAVAILABLE"
                else:
                    recipient = bindings().get(str(target.id))
                    if not recipient:
                        raise SendError("NOT_BOUND")
                    if row.recipient and row.recipient != recipient:
                        raise SendError("BINDING_CHANGED")
                    row.recipient = recipient
                    db.commit()
                    row.message_id = client.send(recipient, row.content)
                    row.status, row.error_code = "SENT", None
            elif not order or order.engineer_id != row.engineer_id or order.status not in ("assigned", "pending", "processing"):
                row.status, row.error_code = "CANCELLED", "ASSIGNMENT_CHANGED"
            elif not engineer or not engineer.user or engineer.user.role != "engineer" or engineer.status != "active":
                row.status, row.error_code = "BLOCKED", "ENGINEER_UNAVAILABLE"
            else:
                recipient = bindings().get(str(engineer.user_id))
                if not recipient:
                    raise SendError("NOT_BOUND")
                if row.recipient and row.recipient != recipient:
                    raise SendError("BINDING_CHANGED")
                row.recipient = recipient
                db.commit()
                row.message_id = client.send(recipient, row.content)
                row.status, row.error_code = "SENT", None
        except SendError as exc:
            row.error_code = exc.code
            row.status = "UNKNOWN" if exc.uncertain else "RETRY" if exc.retry and row.attempts < 5 else "FAILED"
            row.next_attempt_at = datetime.utcnow() + timedelta(seconds=min(900, 30 * 2 ** row.attempts))
        except Exception:
            row.status, row.error_code = "UNKNOWN", "INTERNAL_ERROR"
        row.updated_at = datetime.utcnow()
        db.commit()
        return True


_stop = threading.Event()
_thread = None


def start_worker():
    global _thread
    if not enabled() or (_thread and _thread.is_alive()):
        return
    bindings()  # Fail fast on unsafe/broken recipient configuration.
    _stop.clear()

    def run():
        client = WeComClient()
        while not _stop.wait(2):
            try:
                process_one(client)
            except Exception:
                # Persistent outbox survives restart; never log secret-bearing exceptions.
                _stop.wait(10)

    _thread = threading.Thread(target=run, daemon=True, name="wecom-outbox")
    _thread.start()


def stop_worker():
    _stop.set()
    if _thread:
        _thread.join(timeout=25)
