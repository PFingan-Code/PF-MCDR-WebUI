"""多服面板合并 / 快速配对 API。

- `/servers`：负载迁入统一外壳 `data.servers`；`isLocal` 驼峰字段改名 `local`。
- `/panel_merge_config`：GET 读（外壳 data），POST 改 PUT + `PanelMergeConfigRequest`
  结构化 body（`panel_role` Literal 枚举，非法值 422 统一错误体；写失败 500
  `config_write_failed`）。
- 配对状态机字段 `status`（pending/accepted/denied）改名为 `phase`，外壳只保留
  success|error；`expires_at` 收敛为 epoch 秒。
- 所有 `await request.json()` 手写取值替换为 Pydantic 入参模型；
  失败路径抛 `BusinessException`（显式状态码 + 机器码）。

跨服契约注意：主服 `connect_request/connect_status` 会调用子服 `/api/pairing/request|status`，
本仓库前后端同版本发布，两侧同时切换为 `data.phase` 契约。
"""

from __future__ import annotations

import datetime
import json
import secrets
import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from guguwebui.dependencies.auth import get_current_admin, get_current_user
from guguwebui.panel_merge.state import get_pairing_state, now_utc
from guguwebui.services.config_service import ConfigService
from guguwebui.structures import (
    BusinessException,
    PanelMergeConfigRequest,
    PairingConnectRequest,
    PairingDecisionRequest,
    PairingRequest,
)
from guguwebui.structures.envelope import ApiSuccessEnvelope, success
from guguwebui.utils.request_security import get_client_ip

router = APIRouter()


def _require_slave(request: Request) -> None:
    """仅子服模式可用的动作：非子服 → 400 role_mismatch。"""
    cfg = request.app.state.config_service.get_config()
    if cfg.get("panel_role", "master") != "slave":
        raise BusinessException(
            "仅子服模式可执行该操作",
            status_code=400,
            code="role_mismatch",
        )


def _require_master(request: Request) -> None:
    """仅主服模式可用的动作：非主服 → 400 role_mismatch。"""
    cfg = request.app.state.config_service.get_config()
    if cfg.get("panel_role", "master") != "master":
        raise BusinessException(
            "仅主服模式可执行该操作",
            status_code=400,
            code="role_mismatch",
        )


def _write_panel_config(request: Request, cfg: dict) -> None:
    """把面板合并相关配置写回 config.json（失败 → 500 config_write_failed）。"""
    try:
        config_dir = request.app.state.server_interface.get_data_folder()
        Path(config_dir).mkdir(parents=True, exist_ok=True)
        config_path = Path(config_dir) / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
    except Exception as e:
        raise BusinessException(
            f"保存配置失败: {str(e)}",
            status_code=500,
            code="config_write_failed",
        )


@router.get("/servers", response_model=ApiSuccessEnvelope)
async def api_list_servers(request: Request, user: dict = Depends(get_current_user)):
    """获取可用的服务器列表（主服 + 子服）"""
    config_service: ConfigService = request.app.state.config_service
    cfg = config_service.get_config()
    servers: List[Dict[str, Any]] = [
        {"id": "local", "name": "local", "enabled": True, "local": True}
    ]
    for s in (cfg.get("panel_slaves") or []):
        if not isinstance(s, dict):
            continue
        servers.append(
            {
                "id": str(s.get("id", "")).strip(),
                "name": s.get("name") or s.get("id") or "",
                "enabled": bool(s.get("enabled", True)),
                "local": False,
            }
        )
    servers = [x for x in servers if x.get("id")]
    return JSONResponse(success({"servers": servers}))


@router.get("/panel_merge_config", response_model=ApiSuccessEnvelope)
async def api_get_panel_merge_config(
    request: Request, admin: dict = Depends(get_current_admin)
):
    config_service: ConfigService = request.app.state.config_service
    cfg = config_service.get_config()
    return JSONResponse(
        success(
            {
                "panel_role": cfg.get("panel_role", "master"),
                "panel_slaves": cfg.get("panel_slaves") or [],
                "panel_master": cfg.get("panel_master")
                or {"allowed_tokens": [], "allowed_master_ips": []},
            }
        )
    )


@router.put("/panel_merge_config", response_model=ApiSuccessEnvelope)
async def api_save_panel_merge_config(
    request: Request,
    body: PanelMergeConfigRequest,
    admin: dict = Depends(get_current_admin),
):
    """保存面板合并配置（原 POST /panel_merge_config 改 PUT，body 结构化）"""
    config_service: ConfigService = request.app.state.config_service
    cfg = config_service.get_config()

    cfg["panel_role"] = body.panel_role
    cfg["panel_slaves"] = (
        body.panel_slaves if body.panel_slaves is not None else []
    )
    cfg["panel_master"] = (
        body.panel_master
        if body.panel_master is not None
        else cfg.get("panel_master") or {"allowed_tokens": [], "allowed_master_ips": []}
    )

    _write_panel_config(request, cfg)
    return JSONResponse(success(message="配置已保存"))


# ============================================================#
# Pairing APIs (Quick Mode)


@router.post("/pairing/enable", response_model=ApiSuccessEnvelope)
async def api_pairing_enable(request: Request, admin: dict = Depends(get_current_admin)):
    """子服开启约 5 分钟接受窗口，返回窗口截止时间（epoch 秒）"""
    _require_slave(request)
    st = get_pairing_state(request.app)
    expires = now_utc() + datetime.timedelta(minutes=5)
    st["enabled_until"] = expires
    return JSONResponse(success({"expires_at": int(expires.timestamp())}))


@router.post("/pairing/disable", response_model=ApiSuccessEnvelope)
async def api_pairing_disable(request: Request, admin: dict = Depends(get_current_admin)):
    """关闭接受窗口并清空 pending"""
    _require_slave(request)
    st = get_pairing_state(request.app)
    st["enabled_until"] = None
    st["pending"] = {}
    return JSONResponse(success(message="已停止接受连接"))


@router.post("/pairing/request", response_model=ApiSuccessEnvelope)
async def api_pairing_request(request: Request, body: PairingRequest):
    """
    主服 -> 子服：发起连接请求
    - 不需要登录（仅在 enable 窗口内有效）
    - 收到第一个请求后关闭窗口
    """
    _require_slave(request)

    st = get_pairing_state(request.app)
    enabled_until = st.get("enabled_until")
    if not enabled_until or now_utc() > enabled_until:
        raise BusinessException(
            "当前未开启接受连接或已超时",
            status_code=403,
            code="pairing_window_closed",
        )

    # 收到第一个请求后关闭窗口
    st["enabled_until"] = None

    master_name = (body.master_name or "").strip()
    client_ip = get_client_ip(request)

    request_id = uuid.uuid4().hex
    st.setdefault("pending", {})[request_id] = {
        "ip": client_ip,
        "master_name": master_name,
        # created_at 收敛为 epoch 秒（不对外暴露，仅内部记录用）
        "created_at": int(now_utc().timestamp()),
    }
    return JSONResponse(success({"phase": "pending", "request_id": request_id}))


@router.get("/pairing/pending", response_model=ApiSuccessEnvelope)
async def api_pairing_pending(request: Request, admin: dict = Depends(get_current_admin)):
    st = get_pairing_state(request.app)
    pending = st.get("pending") or {}
    items = []
    for rid, rec in pending.items():
        if not isinstance(rec, dict):
            continue
        items.append(
            {
                "request_id": rid,
                "ip": rec.get("ip") or "",
                "master_name": rec.get("master_name") or "",
            }
        )
    return JSONResponse(success({"pending": items}))


@router.post("/pairing/deny", response_model=ApiSuccessEnvelope)
async def api_pairing_deny(
    request: Request,
    body: PairingDecisionRequest,
    admin: dict = Depends(get_current_admin),
):
    st = get_pairing_state(request.app)
    request_id = body.request_id.strip()
    (st.get("pending") or {}).pop(request_id, None)
    st.setdefault("results", {})[request_id] = {"phase": "denied"}
    return JSONResponse(success(message="已拒绝连接请求"))


@router.post("/pairing/accept", response_model=ApiSuccessEnvelope)
async def api_pairing_accept(
    request: Request,
    body: PairingDecisionRequest,
    admin: dict = Depends(get_current_admin),
):
    """接受连接请求：生成 token 并写入子服 panel_master.allowed_tokens"""
    _require_slave(request)
    st = get_pairing_state(request.app)
    request_id = body.request_id.strip()
    rec = (st.get("pending") or {}).pop(request_id, None)
    if not rec:
        raise BusinessException(
            "连接请求不存在或已处理",
            status_code=404,
            code="request_not_found",
        )

    token = secrets.token_urlsafe(24)
    st.setdefault("results", {})[request_id] = {"phase": "accepted", "token": token}

    # 写入子服配置：panel_master.allowed_tokens 追加
    cfg = request.app.state.config_service.get_config()
    panel_master = cfg.get("panel_master") or {"allowed_tokens": [], "allowed_master_ips": []}
    allowed = panel_master.get("allowed_tokens") or []
    if not isinstance(allowed, list):
        allowed = []
    allowed.append({"name": rec.get("master_name") or "master", "token": token, "enabled": True})
    panel_master["allowed_tokens"] = allowed
    cfg["panel_master"] = panel_master

    _write_panel_config(request, cfg)
    return JSONResponse(success(message="已接受连接请求"))


@router.get("/pairing/status", response_model=ApiSuccessEnvelope)
async def api_pairing_status(request: Request, request_id: str):
    """查询配对结果（子服侧；主服 connect_status 轮询此接口）"""
    st = get_pairing_state(request.app)
    result = (st.get("results") or {}).get(request_id)
    if not result:
        return JSONResponse(success({"phase": "pending"}))
    return JSONResponse(success(result))


@router.post("/pairing/connect_request", response_model=ApiSuccessEnvelope)
async def api_pairing_connect_request(
    request: Request,
    body: PairingConnectRequest,
    admin: dict = Depends(get_current_admin),
):
    """
    主服：对外提供“连接子服”的统一入口
    - 由主服本地调用（不代理）
    - 发起对目标子服 /api/pairing/request 的请求
    """
    _require_master(request)

    slave_name = body.slave_name.strip()
    base_url = body.base_url.strip().rstrip("/")
    if not slave_name or not base_url:
        raise BusinessException(
            "slave_name/base_url 不能为空",
            status_code=400,
            code="invalid_payload",
        )

    session = getattr(request.app.state, "http_session", None)
    if session is None:
        raise BusinessException(
            "HTTP session not ready",
            status_code=500,
            code="pairing_session_unavailable",
        )

    connect_id = uuid.uuid4().hex
    try:
        async with session.post(
            f"{base_url}/api/pairing/request",
            json={"master_name": "master"},
            ssl=True,
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                msg = (
                    data.get("message")
                    if isinstance(data, dict) and data.get("message")
                    else "request failed"
                )
                raise BusinessException(
                    msg, status_code=400, code="pairing_request_failed"
                )
    except BusinessException:
        raise
    except Exception:
        raise BusinessException(
            "无法连接目标子服",
            status_code=400,
            code="pairing_request_failed",
        )

    payload = data if isinstance(data, dict) else {}
    slave_result = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if slave_result.get("phase") == "pending":
        request_id = str(slave_result.get("request_id", "")).strip()
        st = get_pairing_state(request.app)
        st.setdefault("connects", {})[connect_id] = {
            "base_url": base_url,
            "request_id": request_id,
            "slave_name": slave_name,
            # created_at 收敛为 epoch 秒
            "created_at": int(now_utc().timestamp()),
        }
        return JSONResponse(success({"phase": "pending", "connect_id": connect_id}))

    raise BusinessException(
        "request failed",
        status_code=400,
        code="pairing_request_failed",
    )


@router.get("/pairing/connect_status", response_model=ApiSuccessEnvelope)
async def api_pairing_connect_status(
    request: Request,
    connect_id: str,
    admin: dict = Depends(get_current_admin),
):
    """主服轮询配对结果；成功时把子服写入 panel_slaves 并返回 accepted 与 server 摘要"""
    st = get_pairing_state(request.app)
    rec = (st.get("connects") or {}).get(connect_id)
    if not rec:
        raise BusinessException(
            "connect_id 不存在或已过期",
            status_code=404,
            code="connect_not_found",
        )

    base_url = str(rec.get("base_url", "")).rstrip("/")
    request_id = str(rec.get("request_id", "")).strip()
    if not base_url or not request_id:
        raise BusinessException(
            "invalid connect record",
            status_code=500,
            code="invalid_connect_record",
        )

    session = getattr(request.app.state, "http_session", None)
    if session is None:
        raise BusinessException(
            "HTTP session not ready",
            status_code=500,
            code="pairing_session_unavailable",
        )

    try:
        async with session.get(
            f"{base_url}/api/pairing/status", params={"request_id": request_id}, ssl=True
        ) as resp:
            data = await resp.json(content_type=None)
    except Exception:
        # 子服暂不可达：视为仍在 pending，由前端继续轮询
        return JSONResponse(success({"phase": "pending"}))

    payload = data if isinstance(data, dict) else {}
    slave_result = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    phase = slave_result.get("phase")

    if phase == "accepted":
        token = str(slave_result.get("token", "")).strip()
        if not token:
            raise BusinessException(
                "missing token",
                status_code=500,
                code="pairing_token_missing",
            )

        # 保存到主服配置：panel_slaves 追加/更新
        config_service: ConfigService = request.app.state.config_service
        cfg = config_service.get_config()
        slaves = cfg.get("panel_slaves") or []
        if not isinstance(slaves, list):
            slaves = []

        # 生成 id：基于名称，保证唯一（与旧实现一致）
        base_id = (
            "".join([c.lower() if c.isalnum() else "_" for c in str(rec.get("slave_name", ""))])
            .strip("_")
            or "slave"
        )
        existing_ids = {str(s.get("id")) for s in slaves if isinstance(s, dict)}
        sid = base_id
        i = 1
        while sid in existing_ids:
            i += 1
            sid = f"{base_id}_{i}"
        slaves.append(
            {
                "id": sid,
                "name": rec.get("slave_name") or sid,
                "base_url": base_url,
                "token": token,
                "enabled": True,
                "verify_tls": True,
            }
        )
        cfg["panel_slaves"] = slaves

        _write_panel_config(request, cfg)

        st.setdefault("connects", {}).pop(connect_id, None)
        return JSONResponse(
            success(
                {
                    "phase": "accepted",
                    "server": {"id": sid, "name": rec["slave_name"], "base_url": base_url},
                }
            )
        )

    if phase == "denied":
        st.setdefault("connects", {}).pop(connect_id, None)
        return JSONResponse(success({"phase": "denied"}))

    return JSONResponse(success({"phase": "pending"}))
