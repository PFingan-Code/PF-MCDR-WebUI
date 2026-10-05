import asyncio
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from guguwebui.dependencies.auth import (get_current_admin, get_current_user,
                                         get_current_user_or_chat_session)
from guguwebui.services.operation_audit_service import record_operation
from guguwebui.structures import BusinessException
from guguwebui.structures.envelope import ApiSuccessEnvelope, success
from guguwebui import state as gugu_state

router = APIRouter()


class PluginEnabledRequest(BaseModel):
    """启用/禁用插件（加载/卸载）"""

    enabled: bool


class PimVersionRequest(BaseModel):
    """PIM 安装/更新请求（目标版本可选，缺省取最新）"""

    version: Optional[str] = None
    repo_url: Optional[str] = None


def _resolve_plugin_icon_path(entry: object) -> tuple[Path, str] | None:
    """Resolve an image icon without allowing access outside the plugin page directory."""
    icon = getattr(entry, "icon", None)
    html_path = getattr(entry, "html_path", None)
    if not isinstance(icon, str) or not icon.strip() or not isinstance(html_path, str):
        return None

    relative_icon_path = Path(icon)
    if relative_icon_path.is_absolute():
        return None

    page_directory = Path(html_path).resolve().parent
    icon_path = (page_directory / relative_icon_path).resolve()
    try:
        icon_path.relative_to(page_directory)
    except ValueError:
        return None

    media_type, _ = mimetypes.guess_type(icon_path.name)
    if not icon_path.is_file() or not media_type or not media_type.startswith("image/"):
        return None
    return icon_path, media_type


@router.get("/i18n/languages", response_model=ApiSuccessEnvelope)
def get_languages(request: Request):
    """返回 /lang 目录下的 json 文件及其显示名称（裸数组迁入统一外壳 data.items）"""
    langs = request.app.state.plugin_service.get_languages()
    return JSONResponse(success({"items": langs}), status_code=200)


@router.get("/plugins", response_model=ApiSuccessEnvelope)
async def get_plugins(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """返回全部插件元数据（data.plugins）；单项查询请用 GET /plugins/{plugin_id}。"""
    plugins = request.app.state.plugin_service.get_plugins_list()
    return success({"plugins": plugins})


@router.get("/plugins/web-pages", response_model=ApiSuccessEnvelope)
async def get_registered_web_pages(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """获取所有已注册的插件网页列表"""
    server_interface = getattr(request.app.state, "server_interface", None)

    def _is_plugin_loaded(pid: str) -> bool:
        if server_interface is None:
            return True
        try:
            # 仅当插件当前仍处于“已加载”状态时，才暴露它的侧边栏页与 API
            return server_interface.get_plugin_instance(pid) is not None
        except Exception:
            # 异常不应导致 WebUI 全挂；保守起见仍然返回 True
            return True

    pages = [
        {
            "id": pid,
            "path": entry.html_path,
            "name": getattr(entry, "name", None),
            "icon": getattr(entry, "icon", None),
        }
        for pid, entry in gugu_state.REGISTERED_PLUGIN_PAGES.items()
        if _is_plugin_loaded(pid)
    ]
    return success({"pages": pages})


@router.get("/plugins/online", response_model=ApiSuccessEnvelope)
async def api_get_online_plugins(
    request: Request,
    repo_url: str | None = None,
    _admin: dict = Depends(get_current_admin),
):
    """获取在线插件目录（原 GET /online-plugins 裸数组，已迁移为资源路径 + 统一外壳）"""
    plugins_list = await request.app.state.plugin_service.get_online_plugins(repo_url)
    return success({"items": plugins_list or []})


@router.get("/plugins/{plugin_id}", response_model=ApiSuccessEnvelope)
async def get_plugin(
    request: Request,
    plugin_id: str,
    _user: dict = Depends(get_current_user_or_chat_session),
):
    """获取单个插件元数据（不存在返回 404）

    公开聊天页页脚需要展示 WebUI 版本，因此该只读接口同样接受聊天会话凭证。
    """
    plugins = request.app.state.plugin_service.get_plugins_list()
    plugin = next((p for p in plugins if p.get("id") == plugin_id), None)
    if plugin is None:
        raise BusinessException(
            f"Plugin not found: {plugin_id}", status_code=404, code="plugin_not_found"
        )
    return success({"plugin": plugin})


@router.get("/plugins/{plugin_id}/repository", response_model=ApiSuccessEnvelope)
async def api_get_plugin_repository(
    request: Request,
    plugin_id: str,
    _user: dict = Depends(get_current_user),
):
    """获取插件所属的仓库信息（原 /pim/plugin_repository）"""
    result = request.app.state.plugin_service.get_plugin_repository(plugin_id)
    if not (isinstance(result, dict) and result.get("status") == "success"):
        raise BusinessException(
            (result.get("message") if isinstance(result, dict) else None)
            or f"Plugin not found in any repository: {plugin_id}",
            status_code=404,
            code="plugin_not_in_repository",
        )
    return success({"repository": result.get("repository")})


@router.get("/plugins/{plugin_id}/versions", response_model=ApiSuccessEnvelope)
async def api_get_plugin_versions(
    request: Request,
    plugin_id: str,
    repo_url: str | None = None,
    _user: dict = Depends(get_current_user),
):
    """获取插件版本列表（原 /pim/plugin_versions；条目含 released_at epoch 秒）"""
    versions = request.app.state.plugin_service.get_plugin_versions(plugin_id, repo_url)
    return success({"versions": versions or []})


@router.post("/plugins/{plugin_id}/install", response_model=ApiSuccessEnvelope)
async def api_pim_install_plugin(
    request: Request,
    plugin_id: str,
    body: PimVersionRequest,
    admin: dict = Depends(get_current_admin),
):
    """安装插件（原 POST /pim/install_plugin）"""
    if plugin_id == "guguwebui":
        raise BusinessException(
            "不允许安装 WebUI 自身", status_code=400, code="webui_self_operation"
        )
    try:
        task_id = await request.app.state.plugin_service.install_plugin(
            plugin_id, body.version, body.repo_url
        )
    except Exception as e:
        raise BusinessException(
            str(e), status_code=500, code="pim_task_create_failed"
        )
    record_operation(
        admin,
        operation_type="pim.install_plugin",
        summary=f"发起 PIM 安装插件: {plugin_id} @ {body.version}",
        detail={"plugin_id": plugin_id, "version": body.version, "task_id": task_id},
    )
    return success({"task_id": task_id})


@router.post("/plugins/{plugin_id}/uninstall", response_model=ApiSuccessEnvelope)
async def api_pim_uninstall_plugin(
    request: Request,
    plugin_id: str,
    admin: dict = Depends(get_current_admin),
):
    """卸载插件（原 POST /pim/uninstall_plugin）"""
    if plugin_id == "guguwebui":
        raise BusinessException(
            "不允许卸载 WebUI 自身", status_code=400, code="webui_self_operation"
        )
    try:
        task_id = await request.app.state.plugin_service.uninstall_plugin(plugin_id)
    except Exception as e:
        raise BusinessException(
            str(e), status_code=500, code="pim_task_create_failed"
        )
    record_operation(
        admin,
        operation_type="pim.uninstall_plugin",
        summary=f"发起 PIM 卸载插件: {plugin_id}",
        detail={"plugin_id": plugin_id, "task_id": task_id},
    )
    return success({"task_id": task_id})


@router.post("/plugins/{plugin_id}/update", response_model=ApiSuccessEnvelope)
async def api_pim_update_plugin(
    request: Request,
    plugin_id: str,
    body: PimVersionRequest,
    admin: dict = Depends(get_current_admin),
):
    """更新插件（原 POST /pim/update_plugin，本质为指定版本安装）"""
    if plugin_id == "guguwebui":
        raise BusinessException(
            "不允许更新 WebUI 自身", status_code=400, code="webui_self_operation"
        )
    try:
        task_id = await request.app.state.plugin_service.install_plugin(
            plugin_id, body.version, body.repo_url
        )
    except Exception as e:
        raise BusinessException(
            str(e), status_code=500, code="pim_task_create_failed"
        )
    record_operation(
        admin,
        operation_type="pim.update_plugin",
        summary=f"发起 PIM 更新插件: {plugin_id} → {body.version}",
        detail={"plugin_id": plugin_id, "version": body.version, "task_id": task_id},
    )
    return success({"task_id": task_id})


@router.put("/plugins/{plugin_id}/enabled", response_model=ApiSuccessEnvelope)
async def api_toggle_plugin(
    request: Request,
    plugin_id: str,
    request_body: PluginEnabledRequest,
    admin: dict = Depends(get_current_admin),
):
    """启用/禁用插件（PUT enabled: true|false，替代 POST /toggle_plugin）"""
    # MCDR 插件操作（enable/disable/load/unload）会阻塞等待执行结果，需放到线程池，避免卡住事件循环
    result = await asyncio.to_thread(
        request.app.state.plugin_service.toggle_plugin,
        plugin_id,
        request_body.enabled,
    )
    if isinstance(result, dict) and result.get("status") != "success":
        raise BusinessException(
            result.get("message") or "切换插件状态失败",
            status_code=400,
            code="plugin_action_failed",
        )
    verb = "启用" if request_body.enabled else "禁用"
    record_operation(
        admin,
        operation_type="plugin.toggle",
        summary=f"{verb}插件: {plugin_id}",
        detail={"plugin_id": plugin_id, "load": request_body.enabled},
    )
    return success(message=result.get("message") or f"插件 {plugin_id} 已{'启用' if request_body.enabled else '禁用'}")


@router.post("/plugins/{plugin_id}/reload", response_model=ApiSuccessEnvelope)
async def api_reload_plugin(
    request: Request,
    plugin_id: str,
    admin: dict = Depends(get_current_admin),
):
    """重载插件（替代 POST /reload_plugin）"""
    # MCDR 插件操作（reload）会阻塞等待执行结果，需放到线程池，避免卡住事件循环
    result = await asyncio.to_thread(
        request.app.state.plugin_service.reload_plugin,
        plugin_id,
    )
    if isinstance(result, dict) and result.get("status") != "success":
        raise BusinessException(
            result.get("message") or "重载插件失败",
            status_code=400,
            code="plugin_action_failed",
        )
    record_operation(
        admin,
        operation_type="plugin.reload",
        summary=f"重载插件: {plugin_id}",
        detail={"plugin_id": plugin_id},
    )
    return success(message=result.get("message") or f"插件 {plugin_id} 已重载")


@router.get("/plugins/{plugin_id}/config-files", response_model=ApiSuccessEnvelope)
async def api_list_config_files(
    request: Request,
    plugin_id: str,
    _user: dict = Depends(get_current_user),
):
    """列出插件的配置文件"""
    files_list = request.app.state.config_service.list_config_files(plugin_id)
    return success({"files": files_list})


@router.get("/plugins/web-pages/{plugin_id}/icon")
async def get_registered_web_page_icon(
    request: Request,
    plugin_id: str,
    _user: dict = Depends(get_current_user),
):
    """Return a registered plugin page's relative image icon."""
    entry = gugu_state.REGISTERED_PLUGIN_PAGES.get(plugin_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Plugin page not found")

    server_interface = getattr(request.app.state, "server_interface", None)
    if server_interface is not None:
        try:
            if server_interface.get_plugin_instance(plugin_id) is None:
                raise HTTPException(status_code=404, detail="Plugin page not found")
        except HTTPException:
            raise
        except Exception:
            pass

    resolved_icon = _resolve_plugin_icon_path(entry)
    if resolved_icon is None:
        raise HTTPException(status_code=404, detail="Plugin icon not found")
    icon_path, media_type = resolved_icon
    return FileResponse(icon_path, media_type=media_type)

