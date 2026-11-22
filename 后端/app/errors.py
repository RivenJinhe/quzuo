"""统一错误体与错误码（BE-208）。"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.details = details or {}


# 错误码表（与需求文档 BE-208 一一对应）
VALIDATION_ERROR = "VALIDATION_ERROR"
UNAUTHENTICATED = "UNAUTHENTICATED"
TRIAL_EXPIRED_READONLY = "TRIAL_EXPIRED_READONLY"
TRIAL_EXPIRED = "TRIAL_EXPIRED"
NOT_FOUND = "NOT_FOUND"
VERSION_CONFLICT = "VERSION_CONFLICT"
CONFLICT_REQUIREMENTS = "CONFLICT_REQUIREMENTS"
UNGROUNDED_CONTENT = "UNGROUNDED_CONTENT"
SOURCE_INSUFFICIENT = "SOURCE_INSUFFICIENT"
PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
RATE_LIMITED = "RATE_LIMITED"
USERNAME_TAKEN = "USERNAME_TAKEN"
ALREADY_ACCOUNT = "ALREADY_ACCOUNT"
INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
INTERNAL_ERROR = "INTERNAL_ERROR"
GENERATION_TIMEOUT = "GENERATION_TIMEOUT"
STALE_GENERATION = "STALE_GENERATION"


def error_body(code: str, message: str, field: str | None = None, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if field:
        body["error"]["field"] = field
    if details:
        body["error"]["details"] = details
    return body


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=error_body(exc.code, exc.message, exc.field, exc.details),
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    from fastapi.exceptions import RequestValidationError

    if isinstance(exc, RequestValidationError):
        errs = exc.errors()
        first = errs[0] if errs else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        return JSONResponse(
            status_code=422,
            content=error_body(VALIDATION_ERROR, f"参数不合法：{first.get('msg', '格式错误')}", loc or None),
        )
    return JSONResponse(status_code=500, content=error_body(INTERNAL_ERROR, "服务器内部错误"))
