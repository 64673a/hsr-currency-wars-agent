import pytest

from cwagent.config import (
    ConfigError,
    ConfigStore,
    ModelProfile,
    ProviderConfig,
    Roles,
    mask_key,
)

KEY = "sk-abcdef123456"


def prov(name="ds", key=KEY):
    return ProviderConfig(name=name, format="openai", base_url="https://x.example.com/v1/", api_key=key)


def model(name="m1", provider="ds"):
    return ModelProfile(name=name, provider=provider, model="flash", extra_body={"thinking": {"type": "enabled"}})


@pytest.fixture
def store(tmp_path):
    s = ConfigStore(tmp_path / "config.yaml")
    s.upsert_provider(prov())
    s.upsert_model(model())
    return s


def test_endpoint_merges_provider_and_model(store):
    ep = store.load().endpoint("m1")
    assert ep.base_url == "https://x.example.com/v1" and ep.api_key == KEY and ep.model == "flash"
    assert ep.extra_body == {"thinking": {"type": "enabled"}} and ep.format == "openai"
    assert store.load().role_endpoint("tactical").name == "m1"  # 未指定时用第一个档案


def test_provider_key_kept_when_masked_or_empty(store):
    store.upsert_provider(prov(key=mask_key(KEY)), original_name="ds")
    assert store.load().provider("ds").api_key == KEY
    store.upsert_provider(prov(key=""), original_name="ds")
    assert store.load().provider("ds").api_key == KEY
    store.upsert_provider(prov(key="sk-new-0000000"), original_name="ds")
    assert store.load().provider("ds").api_key == "sk-new-0000000"


def test_rename_provider_and_model_updates_refs(store):
    store.update_settings(roles=Roles(tactical="m1"))
    store.upsert_provider(prov("deepseek", key=""), original_name="ds")
    store.upsert_model(model("m2", provider="deepseek"), original_name="m1")
    cfg = store.load()
    assert cfg.models[0].provider == "deepseek" and cfg.roles.tactical == "m2"
    assert cfg.provider("deepseek").api_key == KEY


def test_delete_rules(store):
    with pytest.raises(ConfigError, match="仍被模型档案使用"):
        store.delete_provider("ds")
    store.update_settings(roles=Roles(review="m1"))
    store.delete_model("m1")
    assert store.load().roles.review is None
    store.delete_provider("ds")
    assert store.load().providers == []


def test_reference_errors(store):
    with pytest.raises(ConfigError):
        store.upsert_model(model("m3", provider="nope"))
    with pytest.raises(ConfigError):
        store.update_settings(roles=Roles(tactical="nope"))
    with pytest.raises(ConfigError):
        store.upsert_provider(prov("ds"))  # 重名
    with pytest.raises(ValueError):
        ProviderConfig(name="x", format="openai", base_url="ftp://x")
    with pytest.raises(ValueError):
        ModelProfile(name="a/b", provider="ds", model="m")
