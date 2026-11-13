"""从厂商接口拉取可用模型列表。

- openai 格式:GET {base_url}/models
- anthropic 格式:GET {base_url}/v1/models(分页)
部分兼容网关没有实现模型列表接口,此时返回错误,用户仍可手动填写模型 id。
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from ..config import ProviderConfig
from .base import LLMError
from .client import _error_message


class ModelInfo(BaseModel):
    id: str
    display_name: str = ""
    owned_by: str = ""
    created: str = ""
    context_window: int | None = None
    max_output_tokens: int | None = None


def _models_url(provider: ProviderConfig) -> str:
    base = provider.base_url.rstrip("/")
    if provider.format == "openai":
        return base + "/models"
    return base + ("/models" if base.endswith("/v1") else "/v1/models")


def _headers(provider: ProviderConfig) -> dict[str, str]:
    headers: dict[str, str] = {}
    if provider.format == "openai":
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
    else:
        headers["anthropic-version"] = provider.anthropic_version
        if provider.api_key:
            headers["x-api-key"] = provider.api_key
    headers.update(provider.extra_headers)
    return headers


def _parse(item: dict[str, Any]) -> ModelInfo:
    created = item.get("created_at") or item.get("created") or ""
    return ModelInfo(
        id=str(item.get("id", "")),
        display_name=str(item.get("display_name") or item.get("name") or ""),
        owned_by=str(item.get("owned_by") or ""),
        created=str(created),
        context_window=item.get("max_input_tokens") or item.get("context_length") or item.get("context_window"),
        max_output_tokens=item.get("max_tokens") or item.get("max_output_tokens"),
    )


def list_models(provider: ProviderConfig, *, http: httpx.Client | None = None,
                timeout: float = 30.0) -> list[ModelInfo]:
    own = http is None
    client = http or httpx.Client(timeout=timeout)
    url, headers = _models_url(provider), _headers(provider)
    params: dict[str, Any] = {"limit": 1000} if provider.format == "anthropic" else {}
    models: list[ModelInfo] = []
    try:
        for _ in range(50):  # 分页上限,防止网关返回错误的游标导致死循环
            try:
                resp = client.get(url, headers=headers, params=params)
            except httpx.HTTPError as e:
                raise LLMError(f"请求失败:{type(e).__name__}: {e}") from None
            if resp.status_code != 200:
                raise LLMError(_error_message(resp), status=resp.status_code, body=resp.text[:2000])
            try:
                data = resp.json()
            except ValueError:
                raise LLMError("模型列表不是 JSON", status=200, body=resp.text[:2000]) from None
            items = data.get("data") if isinstance(data, dict) else data
            if not isinstance(items, list):
                raise LLMError("无法识别模型列表的格式", status=200, body=resp.text[:2000])
            models.extend(_parse(i) for i in items if isinstance(i, dict) and i.get("id"))
            if not (isinstance(data, dict) and data.get("has_more") and data.get("last_id")):
                break
            params = {**params, "after_id": data["last_id"]}
    finally:
        if own:
            client.close()
    seen: set[str] = set()
    unique = [m for m in models if not (m.id in seen or seen.add(m.id))]
    return sorted(unique, key=lambda m: m.id)
