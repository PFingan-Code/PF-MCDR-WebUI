import datetime
import secrets
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from guguwebui.constant import DEFALUT_CONFIG, user_db
from guguwebui.utils import totp
from guguwebui.utils.auth_token import is_expired, resolve_web_token
from guguwebui.utils.auth_util import hash_password, verify_password
from guguwebui.utils.rate_limit import (login_retry_after, record_login_failure,
                                        record_login_success,
                                        record_temp_code_failure,
                                        record_totp_failure,
                                        temp_code_retry_after, totp_retry_after)
from guguwebui.utils.request_security import get_client_ip, request_is_https

_LOGIN_SCOPE = "web"

# 账号不存在时也做一次同等开销的哈希校验，避免按响应耗时枚举账号
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


def _too_many_attempts(wait_seconds: int) -> JSONResponse:
    wait_seconds = max(1, int(wait_seconds))
    return JSONResponse(
        {
            "status": "error",
            "code": "too_many_attempts",
            "message": f"登录尝试过于频繁，请 {wait_seconds} 秒后再试。",
            "data": {"retry_after": wait_seconds},
        },
        status_code=429,
        headers={"Retry-After": str(wait_seconds)},
    )


def _is_admin_from_config(server_config: dict, username: str) -> bool:
    disable_other_admin = server_config.get("disable_other_admin", False)
    super_admin_account = str(server_config.get("super_admin_account"))
    if disable_other_admin and str(username) != super_admin_account:
        return False
    return True


def _is_super_admin_from_config(server_config: dict, username: str) -> bool:
    return str(username) == str(server_config.get("super_admin_account"))


def _only_super_admin() -> JSONResponse:
    return JSONResponse(
        {"status": "error", "message": "只有超级管理才能登录。"},
        status_code=403,
    )


class AuthService:
    def __init__(self, server, config_service=None):
        self.server = server
        self.config_service = config_service

    @staticmethod
    def login_admin_check(account, disable_other_admin, super_admin_account):
        if disable_other_admin and str(account) != str(super_admin_account):
            return False
        return True

    def _load_config(self) -> dict:
        return self.server.load_config_simple(
            "config.json", DEFALUT_CONFIG, echo_in_console=False
        )

    def _issue_session(
        self,
        request: Request,
        server_config: dict,
        username: str,
        lifetime: datetime.timedelta,
        body: dict,
    ) -> JSONResponse:
        """签发 token（服务端记录 + HttpOnly cookie），并补齐 is_admin / is_super_admin。"""
        now = datetime.datetime.now(datetime.timezone.utc)
        token = secrets.token_hex(16)
        expiry = now + lifetime

        request.session["logged_in"] = True
        request.session["token"] = token
        request.session["username"] = username

        user_db["token"][token] = {
            "user_name": username,
            "expire_time": str(expiry),
        }
        user_db.save()

        body = dict(body)
        body.setdefault("status", "success")
        body["is_admin"] = _is_admin_from_config(server_config, username)
        body["is_super_admin"] = _is_super_admin_from_config(server_config, username)

        root_path = request.scope.get("root_path", "")
        response = JSONResponse(body)
        response.set_cookie(
            "token",
            token,
            expires=expiry,
            path=root_path if root_path else "/",
            httponly=True,
            samesite="lax",
            secure=request_is_https(request, server_config),
            max_age=int(lifetime.total_seconds()),
        )
        return response

    @staticmethod
    def _account_lifetime(remember: bool) -> datetime.timedelta:
        return datetime.timedelta(days=365) if remember else datetime.timedelta(days=1)

    def _account_session(
        self, request: Request, server_config: dict, account: str, remember: bool, extra: Optional[dict] = None
    ) -> JSONResponse:
        body = {
            "message": "登录成功",
            "nickname": user_db.get("qq_nicknames", {}).get(str(account)),
        }
        body.update(extra or {})
        return self._issue_session(
            request, server_config, account, self._account_lifetime(remember), body
        )

    @staticmethod
    def _totp_challenge(account: str, remember: bool, extra: Optional[dict] = None) -> JSONResponse:
        """第一因素已通过、需要两步验证码：返回票据，由 /api/login/totp 完成登录。

        使用 401 + code=totp_required：不认识该字段的旧前端会按登录失败处理（失败即拒绝）。
        """
        ticket = totp.create_login_ticket(account, remember)
        body = {
            "status": "error",
            "code": "totp_required",
            "message": "该账号已启用两步验证，请输入验证器中的 6 位验证码。",
            "data": {"ticket": ticket, "expires_in": totp.LOGIN_TICKET_SECONDS},
        }
        body.update(extra or {})
        return JSONResponse(body, status_code=401)

    async def login(
        self,
        request: Request,
        account: str,
        password: str,
        temp_code: str,
        remember: bool,
    ):
        now = datetime.datetime.now(datetime.timezone.utc)
        server_config = self._load_config()
        client_ip = get_client_ip(request, server_config)

        if account and password:
            account = account.replace("<", "").replace(">", "")
            password = password.replace("<", "").replace(">", "")
            wait = login_retry_after(_LOGIN_SCOPE, client_ip, account)
            if wait > 0:
                return _too_many_attempts(wait)

            stored_hash = user_db["user"].get(account)
            if stored_hash:
                password_ok = verify_password(password, stored_hash)
            else:
                verify_password(password, _DUMMY_PASSWORD_HASH)
                password_ok = False
            if not password_ok:
                record_login_failure(_LOGIN_SCOPE, client_ip, account)
                return JSONResponse(
                    {"status": "error", "message": "账号或密码错误。"}, status_code=401
                )

            # 先校验密码再判断管理员限制：不向未持有密码的人透露哪个账号是超级管理员
            if not self.login_admin_check(
                account,
                server_config.get("disable_other_admin", False),
                server_config.get("super_admin_account"),
            ):
                return _only_super_admin()

            if totp.is_enabled(account):
                # 两步验证完成前不清除失败计数，避免“密码正确 → 重置计数 → 继续猜验证码”
                return self._totp_challenge(account, remember)

            record_login_success(_LOGIN_SCOPE, client_ip, account)
            return self._account_session(request, server_config, account, remember)

        elif temp_code:
            allow_temp_password = server_config.get("allow_temp_password", True)
            if not allow_temp_password:
                return JSONResponse(
                    {"status": "error", "message": "已禁止临时登录码登录。"},
                    status_code=403,
                )

            # 临时码不对应账号：IP 级 + 全局失败次数限制
            wait = max(login_retry_after(_LOGIN_SCOPE, client_ip), temp_code_retry_after())
            if wait > 0:
                return _too_many_attempts(wait)

            if temp_code not in user_db["temp"]:
                record_login_failure(_LOGIN_SCOPE, client_ip)
                record_temp_code_failure()
                return JSONResponse(
                    {"status": "error", "message": "临时登录码无效。"}, status_code=401
                )

            temp_info = user_db["temp"][temp_code]

            # 兼容旧格式（字符串）和新格式（字典）
            if isinstance(temp_info, dict):
                # 新格式：包含 expire_time 和 qq_id
                expire_time_value = temp_info.get("expire_time", "")
                qq_id = temp_info.get("qq_id")
            else:
                # 旧格式：直接是过期时间字符串
                expire_time_value = temp_info
                qq_id = None
            is_valid = not is_expired(expire_time_value, now)

            if is_valid:
                # 如果有关联的QQ号，使用QQ号作为用户名；否则使用 tempuser
                username = str(qq_id) if qq_id else "tempuser"

                # 与账号密码登录一致：禁用其他管理员时，只有超级管理员可登录。
                # 不消耗临时码，过期后自然失效。
                if not self.login_admin_check(
                    username,
                    server_config.get("disable_other_admin", False),
                    server_config.get("super_admin_account"),
                ):
                    return _only_super_admin()

                # 临时码由 MCDR 控制台 / 机器人管理员签发，本身即带外凭证，不再要求两步验证码
                response = self._issue_session(
                    request,
                    server_config,
                    username,
                    datetime.timedelta(hours=2),
                    {
                        "message": "临时登录成功",
                        "username": username,
                        "nickname": user_db.get("qq_nicknames", {}).get(qq_id) if qq_id else None,
                    },
                )

                # 删除已使用的临时码
                del user_db["temp"][temp_code]
                user_db.save()

                self.server.logger.info(f"临时用户登录成功，用户名: {username}，来源 IP: {client_ip}")
                return response
            else:
                # 临时码已过期或无效，删除它
                if temp_code in user_db["temp"]:
                    del user_db["temp"][temp_code]
                    user_db.save()
                record_login_failure(_LOGIN_SCOPE, client_ip)
                record_temp_code_failure()
                return JSONResponse(
                    {"status": "error", "message": "临时登录码无效或已过期。"}, status_code=401
                )
        else:
            return JSONResponse(
                {"status": "error", "message": "请填写完整的登录信息。"},
                status_code=400,
            )

    async def login_with_account(
        self,
        request: Request,
        account: str,
        remember: bool = False,
        state: Optional[str] = None,
    ):
        """
        QR 扫码登录只需要账号存在（username=uin），无需密码验证；
        账号启用了两步验证时仍需验证码。
        成功时会设置 session + token cookie，返回与现有登录接口一致的 JSON 结构。
        """
        server_config = self._load_config()

        account = (account or "").replace("<", "").replace(">", "").strip()
        if not account:
            return JSONResponse(
                {"status": "error", "message": "无效的账号。"},
                status_code=400,
            )

        disable_other_admin = server_config.get("disable_other_admin", False)
        super_admin_account = str(server_config.get("super_admin_account"))
        if not self.login_admin_check(account, disable_other_admin, super_admin_account):
            return _only_super_admin()

        if account not in user_db["user"]:
            create_cmd = f"!!webui create {account} <password>"
            return JSONResponse(
                {
                    "status": "error",
                    "message": (
                        f"该QQ号({account})尚未注册为 guguwebui 账户。\n"
                        f"请在 MCDR 控制台执行：{create_cmd}\n"
                        "提示：密码可任意设置（用于以后密码登录/修改密码），扫码登录只依赖账号已存在。"
                    ),
                    "state": state,
                },
                status_code=401,
            )

        if totp.is_enabled(account):
            return self._totp_challenge(account, remember, {"state": "totp_required"})

        extra = {"state": state} if state else None
        return self._account_session(request, server_config, account, remember, extra)

    async def complete_totp_login(self, request: Request, ticket: str, code: str):
        """第二步：用登录票据 + 验证码完成登录。"""
        server_config = self._load_config()
        client_ip = get_client_ip(request, server_config)

        info = totp.get_login_ticket(ticket)
        if info is None:
            return JSONResponse(
                {
                    "status": "error",
                    "code": "totp_ticket_invalid",
                    "message": "登录已超时，请重新登录。",
                },
                status_code=401,
            )
        account = info["username"]

        wait = max(login_retry_after(_LOGIN_SCOPE, client_ip, account), totp_retry_after(account))
        if wait > 0:
            return _too_many_attempts(wait)

        # 票据签发后配置可能已变化（如开启了“仅超级管理员”）
        if not self.login_admin_check(
            account,
            server_config.get("disable_other_admin", False),
            server_config.get("super_admin_account"),
        ):
            totp.consume_login_ticket(ticket)
            return _only_super_admin()

        if not totp.verify_code(account, code):
            record_login_failure(_LOGIN_SCOPE, client_ip, account)
            record_totp_failure(account)
            remaining = totp.record_ticket_failure(ticket)
            return JSONResponse(
                {
                    "status": "error",
                    "code": "totp_invalid" if remaining > 0 else "totp_ticket_invalid",
                    "message": "验证码错误。" if remaining > 0 else "验证码错误次数过多，请重新登录。",
                    "data": {"remaining_attempts": remaining},
                },
                status_code=401,
            )

        totp.consume_login_ticket(ticket)
        record_login_success(_LOGIN_SCOPE, client_ip, account)
        return self._account_session(request, server_config, account, info["remember"])

    async def logout(self, request: Request, response):
        """
        清理会话与 token：
        - 清空 session
        - 从 user_db 中移除当前 token
        - 删除常见路径下的 token cookie（独立模式与挂载模式）
        """
        # 清理 session
        request.session["logged_in"] = False
        request.session.clear()

        # 计算根路径，用于 cookie path
        root_path = request.scope.get("root_path", "")
        cookie_path = root_path if root_path else "/"

        # 从 user_db 中移除 token，确保后端登录状态真正失效
        token = request.cookies.get("token")
        try:
            if token and token in user_db.get("token", {}):
                del user_db["token"][token]
                user_db.save()
        except Exception:
            # 不因清理失败中断整个登出流程
            pass

        # 删除 token cookie（当前路径）
        response.set_cookie(
            "token",
            value="",
            path=cookie_path if cookie_path else "/",
            expires=0,
            max_age=0,
            httponly=True,
            samesite="lax",
        )

        # 额外尝试清除常见路径，兼容不同挂载/反向代理场景
        for p in ["/", "/guguwebui", "/guguwebui/"]:
            response.set_cookie(
                "token",
                value="",
                path=p,
                expires=0,
                max_age=0,
                httponly=True,
                samesite="lax",
            )

        return response

    async def check_session_valid(self, request: Request) -> bool:
        """检查会话是否有效"""
        token = request.cookies.get("token")
        server_config = self.server.load_config_simple(
            "config.json", DEFALUT_CONFIG, echo_in_console=False
        )
        disable_other_admin = server_config.get("disable_other_admin", False)
        super_admin_account = server_config.get("super_admin_account")

        user = resolve_web_token(token)
        if user is not None and self.login_admin_check(
            user["username"], disable_other_admin, super_admin_account
        ):
            request.session["logged_in"] = True
            request.session["token"] = token
            request.session["username"] = user["username"]
            return True

        # 如果 token 无效（含过期、被禁止登录），清理
        if token and token in user_db["token"]:
            del user_db["token"][token]
            user_db.save()
        return False
