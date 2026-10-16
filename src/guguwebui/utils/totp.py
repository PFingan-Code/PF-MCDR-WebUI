"""WebUI 账号两步验证（TOTP，RFC 6238：SHA1 / 30 秒 / 6 位，兼容常见验证器 App）。

存储：user_db["totp"][username] = {
    "secret": str | None,          # 已启用的密钥（Base32）
    "enabled": bool,
    "last_step": int,              # 最近一次通过校验的时间步，防止同一验证码重放
    "pending_secret": str | None,  # 绑定流程中、尚未确认的密钥
    "pending_expire": float,       # pending_secret 的过期时间（epoch 秒）
}

登录票据（ticket）：账号密码 / 扫码校验通过、但还需要验证码时签发，仅保存在进程内存，
5 分钟有效、最多尝试 5 次；插件重载后失效（重新登录即可）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from typing import Optional
from urllib.parse import quote, urlencode

TOTP_PERIOD = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1  # 允许前后各 1 个时间步的时钟误差
TOTP_ISSUER = "GUGU WebUI"
PENDING_SETUP_SECONDS = 600

LOGIN_TICKET_SECONDS = 300
LOGIN_TICKET_MAX_ATTEMPTS = 5


# --------------------------------------------------------------------------
# RFC 6238
# --------------------------------------------------------------------------


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    cleaned = secret.strip().replace(" ", "").upper()
    cleaned += "=" * (-len(cleaned) % 8)
    return base64.b32decode(cleaned)


def code_at_step(secret: str, step: int) -> str:
    digest = hmac.new(_decode_secret(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** TOTP_DIGITS)).zfill(TOTP_DIGITS)


def current_step(now: Optional[float] = None) -> int:
    return int((time.time() if now is None else now) // TOTP_PERIOD)


def match_step(secret: str, code: str, now: Optional[float] = None) -> Optional[int]:
    """验证码匹配时返回对应的时间步，否则返回 None。"""
    code = (code or "").strip().replace(" ", "")
    if len(code) != TOTP_DIGITS or not code.isdigit() or not secret:
        return None
    base = current_step(now)
    try:
        for step in range(base - TOTP_WINDOW, base + TOTP_WINDOW + 1):
            if hmac.compare_digest(code_at_step(secret, step), code):
                return step
    except (ValueError, TypeError):
        return None
    return None


def provisioning_uri(secret: str, account: str, issuer: str = TOTP_ISSUER) -> str:
    label = quote(f"{issuer}:{account}", safe="")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": TOTP_DIGITS, "period": TOTP_PERIOD}
    )
    return f"otpauth://totp/{label}?{query}"


# --------------------------------------------------------------------------
# 账号绑定状态
# --------------------------------------------------------------------------

_store_lock = threading.RLock()


def _records() -> dict:
    from guguwebui.constant import user_db

    records = user_db.get("totp")
    if not isinstance(records, dict):
        records = {}
        user_db["totp"] = records
    return records


def _save() -> None:
    from guguwebui.constant import user_db

    user_db.save()


def is_enabled(username: str) -> bool:
    record = _records().get(str(username))
    return bool(isinstance(record, dict) and record.get("enabled") and record.get("secret"))


def begin_setup(username: str, now: Optional[float] = None) -> str:
    """生成待确认的新密钥（不影响已启用的密钥，确认前登录仍用旧密钥）。"""
    now = time.time() if now is None else now
    secret = generate_secret()
    with _store_lock:
        records = _records()
        record = records.get(str(username))
        if not isinstance(record, dict):
            record = {"secret": None, "enabled": False, "last_step": 0}
        record["pending_secret"] = secret
        record["pending_expire"] = now + PENDING_SETUP_SECONDS
        records[str(username)] = record
        _save()
    return secret


def confirm_setup(username: str, code: str, now: Optional[float] = None) -> bool:
    """用验证器生成的验证码确认待绑定密钥；成功后启用两步验证。"""
    now = time.time() if now is None else now
    with _store_lock:
        record = _records().get(str(username))
        if not isinstance(record, dict):
            return False
        pending = record.get("pending_secret")
        if not pending or now > float(record.get("pending_expire") or 0):
            return False
        step = match_step(pending, code, now)
        if step is None:
            return False
        record["secret"] = pending
        record["enabled"] = True
        record["last_step"] = step
        record["pending_secret"] = None
        record["pending_expire"] = 0
        _save()
    return True


def verify_code(username: str, code: str, now: Optional[float] = None) -> bool:
    """校验已启用账号的验证码；同一时间步（及更早）的验证码只能使用一次。"""
    with _store_lock:
        record = _records().get(str(username))
        if not isinstance(record, dict) or not record.get("enabled"):
            return False
        step = match_step(str(record.get("secret") or ""), code, now)
        if step is None or step <= int(record.get("last_step") or 0):
            return False
        record["last_step"] = step
        _save()
    return True


def disable(username: str) -> bool:
    with _store_lock:
        records = _records()
        if str(username) not in records:
            return False
        del records[str(username)]
        _save()
    return True


# --------------------------------------------------------------------------
# 登录票据
# --------------------------------------------------------------------------

_tickets: dict = {}
_tickets_lock = threading.Lock()


def _prune_tickets(now: float) -> None:
    for key in [k for k, v in _tickets.items() if v["expires"] <= now]:
        _tickets.pop(key, None)


def create_login_ticket(username: str, remember: bool, now: Optional[float] = None) -> str:
    now = time.monotonic() if now is None else now
    ticket = secrets.token_urlsafe(24)
    with _tickets_lock:
        _prune_tickets(now)
        _tickets[ticket] = {
            "username": str(username),
            "remember": bool(remember),
            "expires": now + LOGIN_TICKET_SECONDS,
            "attempts": 0,
        }
    return ticket


def get_login_ticket(ticket: str, now: Optional[float] = None) -> Optional[dict]:
    now = time.monotonic() if now is None else now
    with _tickets_lock:
        _prune_tickets(now)
        info = _tickets.get(ticket or "")
        return dict(info) if info else None


def consume_login_ticket(ticket: str) -> None:
    with _tickets_lock:
        _tickets.pop(ticket or "", None)


def record_ticket_failure(ticket: str) -> int:
    """记录一次验证码错误，返回剩余可尝试次数（为 0 时票据已作废）。"""
    with _tickets_lock:
        info = _tickets.get(ticket or "")
        if not info:
            return 0
        info["attempts"] += 1
        remaining = LOGIN_TICKET_MAX_ATTEMPTS - info["attempts"]
        if remaining <= 0:
            _tickets.pop(ticket, None)
            return 0
        return remaining


def clear_login_tickets() -> None:
    """测试用。"""
    with _tickets_lock:
        _tickets.clear()
