import datetime
import json
import os
import secrets
import string
from pathlib import Path

from mcdreforged.api.rtext import RColor, RText, RTextList

from guguwebui.constant import pwd_context, user_db
from guguwebui.utils.server_util import format_host_for_url


def migrate_old_config():
    plugin_config_dir = Path("./config") / "guguwebui"
    config_path = plugin_config_dir / "config.json"
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            old_config = json.load(f)
        need_save = False
        if "deepseek_api_key" in old_config and old_config["deepseek_api_key"] and not old_config.get("ai_api_key"):
            old_config["ai_api_key"] = old_config["deepseek_api_key"]
            need_save = True
        if "deepseek_model" in old_config and old_config["deepseek_model"] and not old_config.get("ai_model"):
            old_config["ai_model"] = old_config["deepseek_model"]
            need_save = True
        if "deepseek_api_key" in old_config:
            del old_config["deepseek_api_key"]
            need_save = True
        if "deepseek_model" in old_config:
            del old_config["deepseek_model"]
            need_save = True
        if need_save:
            plugin_config_dir.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(old_config, f, ensure_ascii=False, indent=4)


# WebUI 管理账号密码最短长度（新建 / 修改时校验，不影响已有账号登录）
MIN_PASSWORD_LENGTH = 8


def password_too_short(password: str) -> bool:
    return len(password or "") < MIN_PASSWORD_LENGTH


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(plain_password):
    return pwd_context.hash(plain_password)


def create_temp_password(qq_id: str = None) -> str:
    """
    生成临时登录码
    
    Parameters
    ----------
    qq_id : str, optional
        关联的QQ号，如果提供则会在登录时使用该QQ号作为用户名
    
    Returns
    -------
    str
        临时登录码
    """
    characters = string.ascii_uppercase + string.digits
    temp_password = ''.join(secrets.choice(characters) for _ in range(6))
    expire_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15)
    
    if qq_id:
        # 如果提供了QQ号，存储为字典格式，包含过期时间和QQ号
        user_db['temp'][temp_password] = {
            "expire_time": str(expire_time),
            "qq_id": str(qq_id)
        }
    else:
        # 向后兼容：如果没有提供QQ号，保持旧格式（字符串）
        user_db['temp'][temp_password] = str(expire_time)
    
    user_db.save()
    return temp_password


def create_user_account(user_name: str, password: str) -> bool:
    if user_name not in user_db['user']:
        user_db['user'][user_name] = pwd_context.hash(password)
        user_db.save()
        return True
    return False


def change_user_account(user_name: str, old_password: str, new_password: str) -> bool:
    if user_name in user_db['user'] and verify_password(old_password, user_db['user'][user_name]):
        set_user_password(user_name, new_password)
        return True
    return False


def set_user_password(user_name: str, new_password: str) -> None:
    """设置密码并注销该账号的全部已登录会话（密码泄露后改密即可踢掉对方）。"""
    from guguwebui.utils.auth_token import revoke_user_tokens

    user_db['user'][user_name] = pwd_context.hash(new_password)
    user_db.save()
    revoke_user_tokens(user_name)


def _reply_password_too_short(src) -> None:
    src.reply(RText(f"密码长度至少 {MIN_PASSWORD_LENGTH} 位！", color=RColor.red))


def create_account_command(src, ctx, host: str, port: int):
    if hasattr(src, 'player') and src.player is not None:
        error_msg = RText("此命令只能在终端中执行！请在MCDR控制台中使用此命令。", color=RColor.red)
        src.reply(error_msg)
        return

    account, password = ctx['account'], ctx['password']
    account = account.replace('<', '').replace('>', '')
    password = password.replace('<', '').replace('>', '')
    if password_too_short(password):
        _reply_password_too_short(src)
        return

    success = create_user_account(account, password)
    if success:
        success_msg = RTextList(
            RText("账户: ", color=RColor.green),
            RText(account, color=RColor.yellow),
            RText(" 创建成功。\n", color=RColor.green),
            RText("guguwebui 地址: ", color=RColor.blue),
            RText(f"http://{format_host_for_url(host)}:{port}", color=RColor.aqua)
        )
        src.reply(success_msg)
    else:
        error_msg = RText("账户已存在！", color=RColor.red)
        src.reply(error_msg)


def change_account_command(src, ctx, host: str, port: int):
    if hasattr(src, 'player') and src.player is not None:
        error_msg = RText("此命令只能在终端中执行！请在MCDR控制台中使用此命令。", color=RColor.red)
        src.reply(error_msg)
        return

    account = ctx['account']
    old_password, new_password = ctx['old password'], ctx['new password']
    account = account.replace('<', '').replace('>', '')
    old_password = old_password.replace('<', '').replace('>', '')
    new_password = new_password.replace('<', '').replace('>', '')
    if password_too_short(new_password):
        _reply_password_too_short(src)
        return

    success = change_user_account(account, old_password, new_password)
    if success:
        success_msg = RTextList(
            RText("账户: ", color=RColor.green),
            RText(account, color=RColor.yellow),
            RText(" 修改成功。\n", color=RColor.green),
            RText("guguwebui 地址: ", color=RColor.blue),
            RText(f"http://{format_host_for_url(host)}:{port}", color=RColor.aqua)
        )
        src.reply(success_msg)
    else:
        error_msg = RText("用户不存在 或 密码错误！", color=RColor.red)
        src.reply(error_msg)


def get_temp_password_command(src, ctx, host: str, port: int):
    if hasattr(src, 'player') and src.player is not None:
        error_msg = RText("此命令只能在终端中执行！请在MCDR控制台中使用此命令。", color=RColor.red)
        src.reply(error_msg)
        return

    temp_password = create_temp_password()
    temp_msg = RTextList(
        RText("临时密码(15分钟后过期): ", color=RColor.yellow),
        RText(temp_password, color=RColor.gold),
        RText("\n", color=RColor.reset),
        RText("guguwebui 地址: ", color=RColor.blue),
        RText(f"http://{format_host_for_url(host)}:{port}", color=RColor.aqua)
    )
    src.reply(temp_msg)


def _console_only(src) -> bool:
    if hasattr(src, 'player') and src.player is not None:
        src.reply(RText("此命令只能在终端中执行！请在MCDR控制台中使用此命令。", color=RColor.red))
        return False
    return True


def reset_totp_command(src, ctx):
    """!!webui 2fa reset <account>：验证器丢失时由控制台解除两步验证，并注销该账号全部会话。"""
    if not _console_only(src):
        return
    from guguwebui.utils import totp
    from guguwebui.utils.auth_token import revoke_user_tokens

    account = ctx['account']
    if not totp.disable(account):
        src.reply(RTextList(
            RText("账户 ", color=RColor.red), RText(account, color=RColor.yellow),
            RText(" 未启用两步验证。", color=RColor.red),
        ))
        return
    revoked = revoke_user_tokens(account)
    src.reply(RTextList(
        RText("已解除账户 ", color=RColor.green), RText(account, color=RColor.yellow),
        RText(f" 的两步验证，并注销 {revoked} 个登录会话。", color=RColor.green),
    ))


def revoke_sessions_command(src, ctx):
    """!!webui logout <account>：注销某账号的全部登录会话（怀疑 cookie 泄露时使用）。"""
    if not _console_only(src):
        return
    from guguwebui.utils.auth_token import revoke_user_tokens

    account = ctx['account']
    revoked = revoke_user_tokens(account)
    src.reply(RTextList(
        RText("已注销账户 ", color=RColor.green), RText(account, color=RColor.yellow),
        RText(f" 的 {revoked} 个登录会话。", color=RColor.green),
    ))


def cleanup_chat_verifications():
    try:
        if 'chat_verification' not in user_db:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        codes_to_delete = []
        for code, rec in list(user_db['chat_verification'].items()):
            try:
                expire_time = datetime.datetime.fromisoformat(rec.get('expire_time', '').replace('Z', '+00:00'))
                if now > expire_time:
                    codes_to_delete.append(code)
            except Exception:
                codes_to_delete.append(code)
        for code in codes_to_delete:
            try:
                del user_db['chat_verification'][code]
            except Exception:
                pass
        if codes_to_delete:
            user_db.save()
    except Exception:
        pass


def verify_chat_code_command(src, ctx):
    code = ctx['code']
    if not hasattr(src, 'player') or src.player is None:
        error_msg = RText("此命令只能由玩家在游戏内使用！", color=RColor.red)
        src.reply(error_msg)
        return

    player_id = src.player
    cleanup_chat_verifications()

    if code not in user_db['chat_verification']:
        error_msg = RTextList(
            RText("验证码 ", color=RColor.red),
            RText(code, color=RColor.yellow),
            RText(" 不存在！", color=RColor.red)
        )
        src.reply(error_msg)
        return

    verification = user_db['chat_verification'][code]
    expire_time = datetime.datetime.fromisoformat(verification['expire_time'].replace('Z', '+00:00'))
    if datetime.datetime.now(datetime.timezone.utc) > expire_time:
        del user_db['chat_verification'][code]
        user_db.save()
        error_msg = RTextList(
            RText("验证码 ", color=RColor.red),
            RText(code, color=RColor.yellow),
            RText(" 已过期！", color=RColor.red)
        )
        src.reply(error_msg)
        return

    if verification.get('used'):
        error_msg = RTextList(
            RText("验证码 ", color=RColor.red),
            RText(code, color=RColor.yellow),
            RText(" 已被使用！", color=RColor.red)
        )
        src.reply(error_msg)
        return

    if verification['player_id'] is not None and verification['player_id'] != player_id:
        error_msg = RTextList(
            RText("验证码 ", color=RColor.red),
            RText(code, color=RColor.yellow),
            RText(" 已被玩家 ", color=RColor.red),
            RText(verification['player_id'], color=RColor.yellow),
            RText(" 使用！", color=RColor.red)
        )
        src.reply(error_msg)
        return

    verification['player_id'] = player_id
    verification['used'] = True
    verification['verified_time'] = str(datetime.datetime.now(datetime.timezone.utc))
    user_db.save()

    success_msg = RTextList(
        RText("验证码 ", color=RColor.green),
        RText(code, color=RColor.yellow),
        RText(" 验证成功！请在聊天页设置密码完成注册。", color=RColor.green)
    )
    src.reply(success_msg)
