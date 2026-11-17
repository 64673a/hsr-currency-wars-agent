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


def test_effort_moved_out_of_old_extra_body():
    """旧档案把思考强度写在 extra_body 里:读进来时搬到 reasoning_effort,思考开关留下(发请求时跟着强度改)。"""
    def load(extra, **kw):
        m = ModelProfile(name="m", provider="ds", model="flash", extra_body=extra, **kw)
        return m.reasoning_effort, m.extra_body

    assert load({"thinking": {"type": "enabled"}, "reasoning_effort": "high"}) == ("high", {"thinking": {"type": "enabled"}})
    assert load({"thinking": {"type": "disabled"}}) == ("none", {"thinking": {"type": "disabled"}})
    assert load({"output_config": {"effort": "max"}}) == ("max", {})
    assert load({"output_config": {"effort": "low", "format": {"type": "json_schema"}}}) == (
        "low", {"output_config": {"format": {"type": "json_schema"}}})
    assert load({"reasoning_effort": "turbo"}) == (None, {"reasoning_effort": "turbo"})   # 不认识的档位照旧透传
    assert load({"reasoning_effort": "high"}, reasoning_effort="low") == ("low", {"reasoning_effort": "high"})


def test_effort_saved_and_passed_to_endpoint(store):
    store.upsert_model(ModelProfile(name="m2", provider="ds", model="flash", reasoning_effort="low"))
    assert store.load().endpoint("m2").reasoning_effort == "low"
    assert store.load().endpoint("m1").reasoning_effort is None
    with pytest.raises(ValueError):
        ModelProfile(name="m3", provider="ds", model="flash", reasoning_effort="turbo")


def test_effort_chosen_at_start_overrides_decision_roles_only(store):
    from cwagent.orchestrator.session import endpoints

    store.upsert_model(ModelProfile(name="quick", provider="ds", model="flash", reasoning_effort="none"))
    store.upsert_model(ModelProfile(name="deep", provider="ds", model="flash", reasoning_effort="high"))
    store.update_settings(roles=Roles(strategic="deep", navigator="quick"))
    cfg = store.load()
    eps = endpoints(cfg, "m1", "max")
    assert eps["default"].reasoning_effort == "max" and eps["navigator"].reasoning_effort == "none"
    eps = endpoints(cfg, None, "low")   # 没选模型时按 roles,开局选的强度同样覆盖战略层
    assert eps["strategic"].name == "deep" and eps["strategic"].reasoning_effort == "low"
    assert endpoints(cfg, None)["strategic"].reasoning_effort == "high"
