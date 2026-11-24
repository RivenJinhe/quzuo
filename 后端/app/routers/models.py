"""模型目录接口：给前端下拉框用，只返回可用性与展示名，绝不返回密钥。"""
from fastapi import APIRouter

from app.config import settings
from app.services import providers

router = APIRouter(prefix="/api/v1/models", tags=["models"])


@router.get("")
def list_models():
    return {"default": providers.default_model_id(), "items": providers.public_catalog(),
            "compare_limit": settings.llm_max_compare}
