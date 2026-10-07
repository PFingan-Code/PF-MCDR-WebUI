from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from guguwebui.dependencies.auth import get_current_admin, get_current_user
from guguwebui.services.operation_audit_service import record_operation
from guguwebui.structures import BusinessException, WebConfigSaveRequest
from guguwebui.structures.envelope import ApiSuccessEnvelope, success

router = APIRouter()


class ConfigFileWriteRequest(BaseModel):
    """写入配置文件：content（原始文本）与 config_data（结构化）二选一"""

    content: Optional[str] = Field(default=None, description="原始文本内容（原 save_config_file 语义）")
    config_data: Any = Field(default=None, description="结构化配置内容（原 save_config 语义）")


class CustomAssetContent(BaseModel):
    content: str = Field(..., description="自定义文件内容")


@router.get("/config/icp-records", response_model=ApiSuccessEnvelope)
async def api_get_icp_records(request: Request):
    """获取ICP备案信息（公开）"""
    state = request.app.state
    records = state.file_service.get_icp_records(state.config_service)
    return success({"icp_records": records.get("icp_records", [])})


@router.get("/web-config", response_model=ApiSuccessEnvelope)
async def api_get_web_config(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """获取Web配置"""
    data = await request.app.state.config_service.get_web_config()
    return success(data)


@router.put("/web-config", response_model=ApiSuccessEnvelope)
async def api_save_web_config(
    request: Request,
    config: WebConfigSaveRequest,
    admin: dict = Depends(get_current_admin),
):
    """保存Web配置（结构化字段，无 action 分派；为 None 的字段不修改）"""
    result = request.app.state.config_service.save_web_config(config)
    changed = {
        field: value
        for field, value in config.model_dump(exclude_unset=True).items()
        if value is not None
    }
    record_operation(
        admin,
        operation_type="webui.save_web_config",
        summary="保存 Web 设置",
        detail={"fields": sorted(changed.keys())},
    )
    return success(message=result.get("message") or "配置已保存")


@router.get("/config-files", response_model=ApiSuccessEnvelope)
async def api_load_config(
    request: Request,
    path: str = Query(..., description="文件路径（受 SafePath 约束）"),
    translation: bool = Query(False, description="是否加载 _lang 翻译文件"),
    type: str = Query("auto", description="解析类型：auto/json/yml/yaml/properties/html"),
    _admin: dict = Depends(get_current_admin),
):
    """加载配置文件，统一返回 {path, type, content, config_data} 文档（仅管理员：
    可读目录含 WebUI 自身配置中的 AI Key、面板 token 等敏感信息）"""
    doc = request.app.state.config_service.load_config(path, translation, type)
    return success(doc)


@router.put("/config-files", response_model=ApiSuccessEnvelope)
async def api_save_config(
    request: Request,
    body: ConfigFileWriteRequest,
    path: str = Query(..., description="文件路径（受 SafePath 约束）"),
    admin: dict = Depends(get_current_admin),
):
    """写入配置文件：content=原始文本（原 save_config_file），config_data=结构化（原 save_config）"""
    has_content = body.content is not None
    has_config = body.config_data is not None
    if has_content == has_config:
        raise BusinessException(
            "请提供 content（原始文本）或 config_data（结构化）二者之一",
            status_code=400,
            code="invalid_body",
        )

    config_service = request.app.state.config_service
    if has_content:
        result = config_service.save_config_file_raw(path, body.content or "")
    else:
        result = config_service.save_config(path, body.config_data)

    record_operation(
        admin,
        operation_type="config.save_config",
        summary=f"保存配置: {path}",
        detail={
            "path": path,
            "mode": "raw" if has_content else "structured",
        },
    )
    return success(message=result.get("message") or "配置文件保存成功")


@router.get("/custom-assets/{kind}", response_model=ApiSuccessEnvelope)
async def get_custom_asset(
    request: Request,
    kind: Literal["css", "js"],
    _user: dict = Depends(get_current_user),
):
    """读取全局自定义文件 overall.css / overall.js"""
    content = request.app.state.file_service.load_custom_file(kind)
    return success({"content": content})


@router.put("/custom-assets/{kind}", response_model=ApiSuccessEnvelope)
async def save_custom_asset(
    request: Request,
    kind: Literal["css", "js"],
    data: CustomAssetContent,
    admin: dict = Depends(get_current_admin),
):
    """保存全局自定义文件 overall.css / overall.js"""
    result = request.app.state.file_service.save_custom_file(kind, data.content)
    record_operation(
        admin,
        operation_type="custom.save_overall_asset",
        summary=f"保存全局自定义文件: {kind}",
        detail={"file": kind, "content_length": len(data.content or "")},
    )
    return success(message=result.get("message") or f"{kind} 保存成功")
