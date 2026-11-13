import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Dialog,
  Input,
  List,
  ListItem,
  NumberInput,
  Option,
  SegmentedControl,
  Select,
  Spinner,
  Textarea,
  Toggle,
} from '@arclux/arc-ui-react';
import { useCallback, useEffect, useState } from 'react';

import { api, type ModelInfo, type ModelProfile, type Provider, type Settings, type TestResult } from '../api';
import { errorText, parseJsonObject, pretty, toast } from '../ui';

const ROLE_NAMES: Record<string, string> = { strategic: '战略层', tactical: '战术层', chooser: '选择类', review: '复盘' };

export default function SettingsPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [editProvider, setEditProvider] = useState<Provider | 'new' | null>(null);
  const [editModel, setEditModel] = useState<ModelProfile | 'new' | null>(null);
  const [picker, setPicker] = useState<Provider | null>(null);
  const [tests, setTests] = useState<Record<string, TestResult | 'running'>>({});

  const reload = useCallback(async () => {
    try {
      const [p, m, s] = await Promise.all([
        api<Provider[]>('/api/providers'),
        api<ModelProfile[]>('/api/models'),
        api<Settings>('/api/config'),
      ]);
      setProviders(p);
      setModels(m);
      setSettings(s);
    } catch (e) {
      toast(`读取配置失败:${errorText(e)}`, 'error');
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  async function removeProvider(p: Provider) {
    if (!window.confirm(`删除厂商 ${p.name}?`)) return;
    try {
      await api(`/api/providers/${encodeURIComponent(p.name)}`, { method: 'DELETE' });
      toast(`已删除厂商 ${p.name}`, 'success');
      reload();
    } catch (e) {
      toast(errorText(e), 'error');
    }
  }

  async function removeModel(m: ModelProfile) {
    if (!window.confirm(`删除模型档案 ${m.name}?`)) return;
    try {
      await api(`/api/models/${encodeURIComponent(m.name)}`, { method: 'DELETE' });
      toast(`已删除模型档案 ${m.name}`, 'success');
      reload();
    } catch (e) {
      toast(errorText(e), 'error');
    }
  }

  async function testModel(name: string) {
    setTests((t) => ({ ...t, [name]: 'running' }));
    try {
      const result = await api<TestResult>(`/api/models/${encodeURIComponent(name)}/test`, { method: 'POST' });
      setTests((t) => ({ ...t, [name]: result }));
    } catch (e) {
      setTests((t) => ({ ...t, [name]: { ok: false, error: errorText(e) } }));
    }
  }

  async function saveSettings(next: Settings) {
    try {
      setSettings(await api<Settings>('/api/config', { method: 'PUT', body: next }));
      toast('已保存', 'success');
    } catch (e) {
      toast(errorText(e), 'error');
    }
  }

  return (
    <div className="page">
      <div className="page-title">
        <h1>设置</h1>
        <span className="muted">配置保存在项目根目录的 config.yaml(不入库)</span>
      </div>

      <div className="section">
        <div className="page-title">
          <h2>厂商</h2>
          <Button size="sm" onClick={() => setEditProvider('new')}>
            添加厂商
          </Button>
        </div>
        <p className="muted">
          任何兼容 OpenAI Chat Completions 或 Anthropic Messages 格式的接口都可以接入。地址和 key 只填一次,模型从厂商接口拉取后选择。
        </p>
        {providers.length === 0 ? (
          <Alert variant="info">还没有厂商。先添加一个,再从它的模型列表里选模型。</Alert>
        ) : (
          <List variant="separated">
            {providers.map((p) => (
              <ListItem key={p.name}>
                <span>
                  <b>{p.name}</b> <Badge size="sm" variant="primary">{p.format}</Badge>
                </span>
                <span slot="description">
                  {p.base_url} · key {p.has_key ? p.api_key : '未填'} · 模型档案:{p.models?.length ? p.models.join('、') : '无'}
                </span>
                <span slot="actions" className="list-actions">
                  <Button size="sm" variant="secondary" onClick={() => setPicker(p)}>
                    选择模型
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditProvider(p)}>
                    编辑
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => removeProvider(p)}>
                    删除
                  </Button>
                </span>
              </ListItem>
            ))}
          </List>
        )}
      </div>

      <div className="section">
        <div className="page-title">
          <h2>模型档案</h2>
          <Button size="sm" disabled={providers.length === 0} onClick={() => setEditModel('new')}>
            添加模型档案
          </Button>
        </div>
        <p className="muted">同一厂商可以建多个档案,用来对比不同模型或同一模型的不同参数(如思考开 / 关)。</p>
        {models.length === 0 ? (
          <Alert variant="info">还没有模型档案。</Alert>
        ) : (
          <List variant="separated">
            {models.map((m) => {
              const t = tests[m.name];
              return (
                <ListItem key={m.name}>
                  <span>
                    <b>{m.name}</b>{' '}
                    <span className="muted">
                      {m.provider} / {m.model}
                    </span>{' '}
                    {m.tool_mode === 'json' && <Badge size="sm">JSON 模式</Badge>}
                  </span>
                  <span slot="description">
                    {Object.keys(m.extra_body).length ? <span className="mono">{JSON.stringify(m.extra_body)}</span> : '无额外参数'}
                    {t && t !== 'running' && <TestLine result={t} />}
                  </span>
                  <span slot="actions" className="list-actions">
                    <Button size="sm" variant="secondary" loading={t === 'running'} onClick={() => testModel(m.name)}>
                      测试
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditModel(m)}>
                      编辑
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => removeModel(m)}>
                      删除
                    </Button>
                  </span>
                </ListItem>
              );
            })}
          </List>
        )}
      </div>

      {settings && <RolesCard settings={settings} models={models} onSave={saveSettings} />}
      {settings && <ParamsCard settings={settings} onSave={saveSettings} />}

      {editProvider && (
        <ProviderDialog
          provider={editProvider === 'new' ? null : editProvider}
          onClose={() => setEditProvider(null)}
          onSaved={() => {
            setEditProvider(null);
            reload();
          }}
        />
      )}
      {editModel && (
        <ModelDialog
          model={editModel === 'new' ? null : editModel}
          providers={providers}
          onClose={() => setEditModel(null)}
          onSaved={() => {
            setEditModel(null);
            reload();
          }}
        />
      )}
      {picker && (
        <ModelPicker
          provider={picker}
          existing={models}
          onClose={() => setPicker(null)}
          onSaved={() => {
            setPicker(null);
            reload();
          }}
        />
      )}
    </div>
  );
}

function TestLine({ result }: { result: TestResult }) {
  if (!result.ok) return <div style={{ color: 'rgb(255,120,120)' }}>测试失败:{result.error}</div>;
  const u = result.usage;
  return (
    <div>
      ✓ {result.latency_s}s · 工具调用{result.tool_call ? '正常' : '缺失'} · 思考内容{result.reasoning ? '有' : '本次未返回'}
      {u && ` · token 入 ${u.input_tokens} / 出 ${u.output_tokens}`}
    </div>
  );
}

function RolesCard({ settings, models, onSave }: { settings: Settings; models: ModelProfile[]; onSave: (s: Settings) => void }) {
  const [roles, setRoles] = useState(settings.roles);
  useEffect(() => setRoles(settings.roles), [settings.roles]);
  const fallback = models[0]?.name ?? '无';
  return (
    <div className="section">
      <h2>决策层使用的模型</h2>
      <Card>
        <div className="grid2">
          {Object.entries(ROLE_NAMES).map(([key, label]) => (
            <Select
              key={key}
              label={label}
              value={(roles as any)[key] ?? ''}
              onArcChange={(e) => setRoles({ ...roles, [key]: e.detail.value || null })}
            >
              <Option value="">默认({fallback})</Option>
              {models.map((m) => (
                <Option key={m.name} value={m.name}>
                  {m.name}
                </Option>
              ))}
            </Select>
          ))}
        </div>
        <div slot="footer" className="row">
          <Button size="sm" onClick={() => onSave({ ...settings, roles })}>
            保存
          </Button>
        </div>
      </Card>
    </div>
  );
}

function ParamsCard({ settings, onSave }: { settings: Settings; onSave: (s: Settings) => void }) {
  const [s, setS] = useState(settings);
  useEffect(() => setS(settings), [settings]);
  return (
    <div className="section">
      <h2>运行参数</h2>
      <Card>
        <div className="grid2">
          <Toggle
            label="给模型看官方阵容指引卡"
            checked={s.agent.use_guides}
            onArcChange={(e) => setS({ ...s, agent: { ...s.agent, use_guides: e.detail.checked } })}
          />
          <NumberInput
            label="每个备战阶段最多调用战术层次数"
            min={1}
            max={20}
            value={s.agent.max_tactical_calls_per_stage}
            onArcChange={(e) => setS({ ...s, agent: { ...s.agent, max_tactical_calls_per_stage: e.detail.value } })}
          />
          <NumberInput
            label="单局时长上限(分钟)"
            min={1}
            value={s.agent.max_run_minutes}
            onArcChange={(e) => setS({ ...s, agent: { ...s.agent, max_run_minutes: e.detail.value } })}
          />
          <NumberInput
            label="知识同步最多抓取阵容数"
            min={0}
            max={2000}
            step={10}
            value={s.knowledge.max_lineups}
            onArcChange={(e) => setS({ ...s, knowledge: { ...s.knowledge, max_lineups: e.detail.value } })}
          />
          <Input
            label="网页监听地址(重启后生效)"
            value={s.server.host}
            onArcInput={(e) => setS({ ...s, server: { ...s.server, host: e.detail.value } })}
          />
          <NumberInput
            label="网页端口(重启后生效)"
            min={1}
            max={65535}
            value={s.server.port}
            onArcChange={(e) => setS({ ...s, server: { ...s.server, port: e.detail.value } })}
          />
        </div>
        <div slot="footer" className="row">
          <Button size="sm" onClick={() => onSave(s)}>
            保存
          </Button>
          {s.server.host !== '127.0.0.1' && s.server.host !== 'localhost' && (
            <span className="muted">监听非本机地址时,局域网内的设备都能访问并控制 agent(没有登录机制)</span>
          )}
        </div>
      </Card>
    </div>
  );
}

function ProviderDialog({ provider, onClose, onSaved }: { provider: Provider | null; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(provider?.name ?? '');
  const [format, setFormat] = useState<Provider['format']>(provider?.format ?? 'openai');
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? '');
  const [apiKey, setApiKey] = useState('');
  const [version, setVersion] = useState(provider?.anthropic_version ?? '2026-10-21');
  const [headers, setHeaders] = useState(pretty(provider?.extra_headers));
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      const body = {
        name: name.trim(),
        format,
        base_url: baseUrl.trim(),
        api_key: apiKey,
        extra_headers: parseJsonObject(headers, '额外请求头'),
        anthropic_version: version.trim() || '2026-10-21',
      };
      if (provider) await api(`/api/providers/${encodeURIComponent(provider.name)}`, { method: 'PUT', body });
      else await api('/api/providers', { method: 'POST', body });
      toast(`已保存厂商 ${body.name}`, 'success');
      onSaved();
    } catch (e) {
      toast(errorText(e), 'error');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open heading={provider ? `编辑厂商 ${provider.name}` : '添加厂商'} size="lg" onArcClose={onClose}>
      <div className="form">
        <Input label="名称(自己起)" value={name} onArcInput={(e) => setName(e.detail.value)} />
        <div>
          <div className="muted" style={{ marginBottom: 6 }}>接口格式</div>
          <SegmentedControl value={format} onArcChange={(e) => setFormat(e.detail.value)}>
            <Option value="openai">OpenAI 格式</Option>
            <Option value="anthropic">Anthropic 格式</Option>
          </SegmentedControl>
        </div>
        <Input
          label="接口地址"
          placeholder={format === 'openai' ? 'https://api.example.com/v1' : 'https://api.example.com'}
          value={baseUrl}
          onArcInput={(e) => setBaseUrl(e.detail.value)}
        />
        <Input
          type="password"
          label="API key"
          placeholder={provider?.has_key ? `已保存 ${provider.api_key},留空表示不修改` : '可留空(本地模型)'}
          value={apiKey}
          onArcInput={(e) => setApiKey(e.detail.value)}
        />
        {format === 'anthropic' && (
          <Input label="anthropic-version 请求头(部分网关只认 2023-06-01)" value={version} onArcInput={(e) => setVersion(e.detail.value)} />
        )}
        <Textarea label="额外请求头(JSON,可留空)" rows={3} value={headers} onArcInput={(e) => setHeaders(e.detail.value)} />
      </div>
      <div slot="footer" className="row">
        <Button variant="ghost" onClick={onClose}>
          取消
        </Button>
        <Button loading={saving} onClick={save}>
          保存
        </Button>
      </div>
    </Dialog>
  );
}

function useProviderModels(providerName: string | null) {
  const [state, setState] = useState<{ loading: boolean; models: ModelInfo[]; error: string }>({
    loading: false,
    models: [],
    error: '',
  });
  useEffect(() => {
    if (!providerName) return;
    let cancelled = false;
    setState({ loading: true, models: [], error: '' });
    api<ModelInfo[]>(`/api/providers/${encodeURIComponent(providerName)}/models`)
      .then((models) => !cancelled && setState({ loading: false, models, error: '' }))
      .catch((e) => !cancelled && setState({ loading: false, models: [], error: errorText(e) }));
    return () => {
      cancelled = true;
    };
  }, [providerName]);
  return state;
}

function ModelPicker({
  provider,
  existing,
  onClose,
  onSaved,
}: {
  provider: Provider;
  existing: ModelProfile[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { loading, models, error } = useProviderModels(provider.name);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [manual, setManual] = useState('');
  const [saving, setSaving] = useState(false);
  const used = new Set(existing.filter((m) => m.provider === provider.name).map((m) => m.model));
  const names = new Set(existing.map((m) => m.name));

  async function add() {
    const ids = [...checked, ...manual.split(/[,，\s]+/).filter(Boolean)];
    if (!ids.length) return;
    setSaving(true);
    let added = 0;
    for (const id of ids) {
      let name = id;
      if (names.has(name)) name = `${provider.name}-${id}`;
      try {
        await api('/api/models', {
          method: 'POST',
          body: { name, provider: provider.name, model: id, extra_body: {}, extra_headers: {}, tool_mode: 'native', timeout_seconds: 300 },
        });
        names.add(name);
        added++;
      } catch (e) {
        toast(`${id}:${errorText(e)}`, 'error');
      }
    }
    setSaving(false);
    if (added) toast(`已添加 ${added} 个模型档案,可以在列表里测试或编辑参数`, 'success');
    onSaved();
  }

  return (
    <Dialog open heading={`从 ${provider.name} 选择模型`} size="lg" onArcClose={onClose}>
      <div className="form">
        {loading && (
          <div className="row">
            <Spinner size="sm" /> 正在拉取模型列表…
          </div>
        )}
        {error && <Alert variant="warning">{`拉取模型列表失败:${error}。该接口可能不提供模型列表,可以在下面手动填写模型 id。`}</Alert>}
        {models.length > 0 && (
          <div style={{ maxHeight: 360, overflow: 'auto' }}>
            {models.map((m) => (
              <div key={m.id} className="row" style={{ padding: '4px 0' }}>
                <Checkbox
                  label={m.id}
                  checked={checked.has(m.id)}
                  disabled={used.has(m.id)}
                  onArcChange={(e) => {
                    const next = new Set(checked);
                    if (e.detail.checked) next.add(m.id);
                    else next.delete(m.id);
                    setChecked(next);
                  }}
                />
                <span className="muted">
                  {[m.display_name, m.context_window ? `上下文 ${m.context_window}` : '', used.has(m.id) ? '已添加' : '']
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </div>
            ))}
          </div>
        )}
        <Input label="手动填写模型 id(多个用逗号分隔)" value={manual} onArcInput={(e) => setManual(e.detail.value)} />
      </div>
      <div slot="footer" className="row">
        <Button variant="ghost" onClick={onClose}>
          取消
        </Button>
        <Button loading={saving} disabled={!checked.size && !manual.trim()} onClick={add}>
          添加为模型档案
        </Button>
      </div>
    </Dialog>
  );
}

function ModelDialog({
  model,
  providers,
  onClose,
  onSaved,
}: {
  model: ModelProfile | null;
  providers: Provider[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(model?.name ?? '');
  const [provider, setProvider] = useState(model?.provider ?? providers[0]?.name ?? '');
  const [modelId, setModelId] = useState(model?.model ?? '');
  const [maxTokens, setMaxTokens] = useState(model?.max_tokens ? String(model.max_tokens) : '');
  const [toolMode, setToolMode] = useState<ModelProfile['tool_mode']>(model?.tool_mode ?? 'native');
  const [timeout, setTimeoutS] = useState(model?.timeout_seconds ?? 300);
  const [extraBody, setExtraBody] = useState(pretty(model?.extra_body));
  const [extraHeaders, setExtraHeaders] = useState(pretty(model?.extra_headers));
  const [saving, setSaving] = useState(false);
  const available = useProviderModels(provider || null);

  async function save() {
    setSaving(true);
    try {
      const body = {
        name: name.trim() || modelId.trim(),
        provider,
        model: modelId.trim(),
        max_tokens: maxTokens.trim() ? Number(maxTokens) : null,
        extra_body: parseJsonObject(extraBody, '额外请求参数'),
        extra_headers: parseJsonObject(extraHeaders, '额外请求头'),
        tool_mode: toolMode,
        timeout_seconds: timeout,
      };
      if (model) await api(`/api/models/${encodeURIComponent(model.name)}`, { method: 'PUT', body });
      else await api('/api/models', { method: 'POST', body });
      toast(`已保存模型档案 ${body.name}`, 'success');
      onSaved();
    } catch (e) {
      toast(errorText(e), 'error');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open heading={model ? `编辑模型档案 ${model.name}` : '添加模型档案'} size="lg" onArcClose={onClose}>
      <div className="form">
        <Select label="厂商" value={provider} onArcChange={(e) => setProvider(e.detail.value)}>
          {providers.map((p) => (
            <Option key={p.name} value={p.name}>
              {p.name}({p.format})
            </Option>
          ))}
        </Select>
        <Input label="模型 id" value={modelId} onArcInput={(e) => setModelId(e.detail.value)} />
        {available.models.length > 0 && (
          <Select label="或从厂商的模型列表中选择" value={modelId} onArcChange={(e) => setModelId(e.detail.value)}>
            {available.models.map((m) => (
              <Option key={m.id} value={m.id}>
                {m.id}
              </Option>
            ))}
          </Select>
        )}
        <Input label="档案名(默认与模型 id 相同)" value={name} onArcInput={(e) => setName(e.detail.value)} />
        <div className="grid2">
          <Input label="max_tokens(可留空)" value={maxTokens} onArcInput={(e) => setMaxTokens(e.detail.value)} />
          <NumberInput label="超时(秒)" min={10} max={3600} value={timeout} onArcChange={(e) => setTimeoutS(e.detail.value)} />
        </div>
        <div>
          <div className="muted" style={{ marginBottom: 6 }}>工具调用方式</div>
          <SegmentedControl value={toolMode} onArcChange={(e) => setToolMode(e.detail.value)}>
            <Option value="native">原生工具调用</Option>
            <Option value="json">JSON 模式(不支持工具调用的模型)</Option>
          </SegmentedControl>
        </div>
        <Textarea
          label='额外请求参数(JSON,原样合并进请求体,如 {"reasoning_effort": "high"})'
          rows={4}
          value={extraBody}
          onArcInput={(e) => setExtraBody(e.detail.value)}
        />
        <Textarea label="额外请求头(JSON,可留空)" rows={2} value={extraHeaders} onArcInput={(e) => setExtraHeaders(e.detail.value)} />
      </div>
      <div slot="footer" className="row">
        <Button variant="ghost" onClick={onClose}>
          取消
        </Button>
        <Button loading={saving} disabled={!provider || !modelId.trim()} onClick={save}>
          保存
        </Button>
      </div>
    </Dialog>
  );
}
