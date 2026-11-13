"""config.yaml 的读写。字段含义见 docs/TECH_DESIGN.md 第 11.2 节。

两层结构:
- providers(厂商 / 接口):接口格式 + 地址 + key,只填一次,可从接口拉取可用模型列表;
- models(模型档案):选一个厂商 + 一个模型 id,再加上该模型自己的参数(思考开关等)。
同一厂商可以建多个模型档案,用来对比不同模型或不同参数。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import paths

DEFAULT_ANTHROPIC_VERSION = "2026-10-21"
ApiFormat = Literal["openai", "anthropic"]


def _check_name(v: str) -> str:
    v = v.strip()
    if not v or any(c in v for c in "/\\?#"):
        raise ValueError("名称不能为空,也不能包含 / \\ ? #")
    return v


class ProviderConfig(BaseModel):
    """一个接口提供方:格式 + 地址 + key。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    format: ApiFormat
    base_url: str = Field(min_length=1)
    api_key: str = ""
    extra_headers: dict[str, str] = Field(default_factory=dict)
    anthropic_version: str = DEFAULT_ANTHROPIC_VERSION   # 仅 anthropic 格式使用

    _name = field_validator("name")(classmethod(lambda cls, v: _check_name(v)))

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return v


class ModelProfile(BaseModel):
    """一个模型档案:厂商 + 模型 id + 透传参数。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    max_tokens: int | None = Field(default=None, ge=1)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    tool_mode: Literal["native", "json"] = "native"
    timeout_seconds: float = Field(default=300, gt=0)

    _name = field_validator("name")(classmethod(lambda cls, v: _check_name(v)))


class ModelEndpoint(BaseModel):
    """厂商与模型档案合并后的完整调用参数,LLM 接入层只认这个。"""

    name: str
    format: ApiFormat
    base_url: str
    api_key: str = ""
    model: str
    max_tokens: int | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    tool_mode: Literal["native", "json"] = "native"
    timeout_seconds: float = 300
    anthropic_version: str = DEFAULT_ANTHROPIC_VERSION

    @classmethod
    def of(cls, provider: ProviderConfig, profile: ModelProfile) -> "ModelEndpoint":
        return cls(
            name=profile.name, format=provider.format, base_url=provider.base_url,
            api_key=provider.api_key, model=profile.model, max_tokens=profile.max_tokens,
            extra_body=profile.extra_body,
            extra_headers={**provider.extra_headers, **profile.extra_headers},
            tool_mode=profile.tool_mode, timeout_seconds=profile.timeout_seconds,
            anthropic_version=provider.anthropic_version,
        )


class Roles(BaseModel):
    """各决策层使用的模型档案名;为空时使用第一个模型档案。"""

    tactical: str | None = None
    strategic: str | None = None
    chooser: str | None = None
    review: str | None = None


class AgentSettings(BaseModel):
    use_guides: bool = True
    max_tactical_calls_per_stage: int = Field(default=3, ge=1, le=20)
    max_run_minutes: int = Field(default=120, ge=1)


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8770, ge=1, le=65535)


class KnowledgeSettings(BaseModel):
    max_lineups: int = Field(default=300, ge=0, le=2000)
    request_interval: float = Field(default=1.0, ge=1.0)


class AppConfig(BaseModel):
    providers: list[ProviderConfig] = Field(default_factory=list)
    models: list[ModelProfile] = Field(default_factory=list)
    roles: Roles = Field(default_factory=Roles)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)

    def provider(self, name: str) -> ProviderConfig | None:
        return next((p for p in self.providers if p.name == name), None)

    def model(self, name: str) -> ModelProfile | None:
        return next((m for m in self.models if m.name == name), None)

    def endpoint(self, name: str) -> ModelEndpoint:
        profile = self.model(name)
        if profile is None:
            raise ConfigError(f"模型档案 {name} 不存在")
        provider = self.provider(profile.provider)
        if provider is None:
            raise ConfigError(f"模型档案 {name} 引用的厂商 {profile.provider} 不存在")
        return ModelEndpoint.of(provider, profile)

    def role_endpoint(self, role: str) -> ModelEndpoint:
        name = getattr(self.roles, role) or (self.models[0].name if self.models else None)
        if name is None:
            raise ConfigError("还没有配置任何模型档案")
        return self.endpoint(name)


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"


class ConfigError(ValueError):
    pass


class ConfigStore:
    """线程安全地读写 config.yaml。文件不存在时使用默认值,第一次保存时创建。"""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.config_file()
        self._lock = threading.RLock()

    def load(self) -> AppConfig:
        with self._lock:
            if not self.path.exists():
                return AppConfig()
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            cfg = AppConfig.model_validate(raw)
            self._check_refs(cfg)
            return cfg

    def save(self, cfg: AppConfig) -> None:
        with self._lock:
            self._check_refs(cfg)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = cfg.model_dump(mode="json", exclude_defaults=False)
            text = "# 本机配置,不入库。字段说明见 config.example.yaml。\n" + yaml.safe_dump(
                data, allow_unicode=True, sort_keys=False)
            tmp = self.path.with_suffix(".yaml.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)

    @staticmethod
    def _check_refs(cfg: AppConfig) -> None:
        for kind, items in (("厂商", cfg.providers), ("模型档案", cfg.models)):
            names = [i.name for i in items]
            dup = {n for n in names if names.count(n) > 1}
            if dup:
                raise ConfigError(f"{kind}名称重复:{', '.join(sorted(dup))}")
        providers = {p.name for p in cfg.providers}
        for m in cfg.models:
            if m.provider not in providers:
                raise ConfigError(f"模型档案 {m.name} 引用的厂商 {m.provider} 不存在")
        models = {m.name for m in cfg.models}
        for field, value in cfg.roles.model_dump().items():
            if value is not None and value not in models:
                raise ConfigError(f"决策层 {field} 引用的模型档案 {value} 不存在")

    # ---- 厂商

    def upsert_provider(self, provider: ProviderConfig, *, original_name: str | None = None) -> ProviderConfig:
        """original_name 为空时新建(重名报错),否则修改该厂商。

        修改时 api_key 为空或等于掩码则沿用原来的 key;改名时同步模型档案的引用。
        """
        with self._lock:
            cfg = self.load()
            if original_name is None:  # 新建
                if cfg.provider(provider.name) is not None:
                    raise ConfigError(f"厂商 {provider.name} 已存在")
                cfg.providers.append(provider)
                self.save(cfg)
                return provider
            target = original_name
            old = cfg.provider(target)
            if old is None:
                raise ConfigError(f"厂商 {original_name} 不存在")
            if provider.api_key in ("", mask_key(old.api_key)):
                provider = provider.model_copy(update={"api_key": old.api_key})
            if provider.name != target and cfg.provider(provider.name) is not None:
                raise ConfigError(f"厂商 {provider.name} 已存在")
            cfg.providers[cfg.providers.index(old)] = provider
            for m in cfg.models:
                if m.provider == target:
                    m.provider = provider.name
            self.save(cfg)
            return provider

    def delete_provider(self, name: str) -> None:
        with self._lock:
            cfg = self.load()
            old = cfg.provider(name)
            if old is None:
                raise ConfigError(f"厂商 {name} 不存在")
            used = [m.name for m in cfg.models if m.provider == name]
            if used:
                raise ConfigError(f"厂商 {name} 仍被模型档案使用:{', '.join(used)}")
            cfg.providers.remove(old)
            self.save(cfg)

    # ---- 模型档案

    def upsert_model(self, profile: ModelProfile, *, original_name: str | None = None) -> ModelProfile:
        """original_name 为空时新建(重名报错),否则修改该档案;改名时同步各决策层的引用。"""
        with self._lock:
            cfg = self.load()
            if cfg.provider(profile.provider) is None:
                raise ConfigError(f"厂商 {profile.provider} 不存在")
            if original_name is None:  # 新建
                if cfg.model(profile.name) is not None:
                    raise ConfigError(f"模型档案 {profile.name} 已存在")
                cfg.models.append(profile)
            else:
                target = original_name
                old = cfg.model(target)
                if old is None:
                    raise ConfigError(f"模型档案 {original_name} 不存在")
                if profile.name != target and cfg.model(profile.name) is not None:
                    raise ConfigError(f"模型档案 {profile.name} 已存在")
                cfg.models[cfg.models.index(old)] = profile
                for field in Roles.model_fields:
                    if getattr(cfg.roles, field) == target:
                        setattr(cfg.roles, field, profile.name)
            self.save(cfg)
            return profile

    def delete_model(self, name: str) -> None:
        with self._lock:
            cfg = self.load()
            old = cfg.model(name)
            if old is None:
                raise ConfigError(f"模型档案 {name} 不存在")
            cfg.models.remove(old)
            for field in Roles.model_fields:
                if getattr(cfg.roles, field) == name:
                    setattr(cfg.roles, field, None)
            self.save(cfg)

    # ---- 其他设置

    def update_settings(self, *, roles: Roles | None = None, agent: AgentSettings | None = None,
                        server: ServerSettings | None = None,
                        knowledge: KnowledgeSettings | None = None) -> AppConfig:
        with self._lock:
            cfg = self.load()
            if roles is not None:
                cfg.roles = roles
            if agent is not None:
                cfg.agent = agent
            if server is not None:
                cfg.server = server
            if knowledge is not None:
                cfg.knowledge = knowledge
            self.save(cfg)
            return cfg
