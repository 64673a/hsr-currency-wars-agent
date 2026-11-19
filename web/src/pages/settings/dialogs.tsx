import { useEffect, useMemo, useState } from 'react';

import { Alert } from '@/components/arc/alert/alert';
import { Button } from '@/components/arc/button/button';
import { Checkbox } from '@/components/arc/checkbox/checkbox';
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog';
import { Input } from '@/components/arc/input/input';
import { NumberField } from '@/components/arc/number-field/number-field';
import { SearchField } from '@/components/arc/search-field/search-field';
import SegmentedControl from '@/components/arc/segmented-control/segmented-control';
import { Select } from '@/components/arc/select/select';
import { Skeleton } from '@/components/arc/skeleton/skeleton';
import { Textarea } from '@/components/arc/textarea/textarea';
import { api, EFFORTS, enc, errorText, parseJsonObject, pretty, type Effort, type ModelInfo, type ModelProfile, type Provider } from '@/lib/api';
import page from '@/lib/page.module.css';
import { isOpenCodeGo } from '@/lib/providers';

import styles from './dialogs.module.css';

const FORMATS = [
  { value: 'openai', label: 'OpenAI 格式' },
  { value: 'anthropic', label: 'Anthropic 格式' },
];

function Footer({ onClose, saving, disabled, label, onSave }: { onClose: () => void; saving: boolean; disabled?: boolean; label: string; onSave: () => void }) {
  return (
    <div className={page.actions}>
      <Button variant="ghost" onClick={onClose}>
        取消
      </Button>
      <Button loading={saving} disabled={disabled} onClick={onSave}>
        {label}
      </Button>
    </div>
  );
}

export function ProviderDialog({ provider, onClose, onSaved }: { provider: Provider | null; onClose: () => void; onSaved: (created: Provider | null) => void }) {
  const [name, setName] = useState(provider?.name ?? '');
  const [format, setFormat] = useState<Provider['format']>(provider?.format ?? 'openai');
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? '');
  const [apiKey, setApiKey] = useState('');
  const [version, setVersion] = useState(provider?.anthropic_version ?? '2026-10-21');
  const [headers, setHeaders] = useState(pretty(provider?.extra_headers));
  const [openCodeGo, setOpenCodeGo] = useState<boolean | null>(provider?.opencode_go ?? null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function save() {
    setSaving(true);
    setError('');
    try {
      const body = {
        name: name.trim(),
        format,
        base_url: baseUrl.trim(),
        api_key: apiKey,
        opencode_go: openCodeGo ?? isOpenCodeGo(baseUrl),
        extra_headers: parseJsonObject(headers, '额外请求头'),
        anthropic_version: version.trim() || '2026-10-21',
      };
      const saved = provider
        ? await api<Provider>(`/api/providers/${enc(provider.name)}`, { method: 'PUT', body })
        : await api<Provider>('/api/providers', { method: 'POST', body });
      onSaved(provider ? null : saved);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent title={provider ? `编辑 ${provider.name}` : '添加厂商'}>
        <div className={styles.form}>
          <SegmentedControl label="接口格式" options={FORMATS} value={format} onValueChange={(v) => setFormat(v as Provider['format'])} fullWidth />
          <Input label="名称" value={name} onChange={(e) => setName(e.target.value)} />
          <Input
            label="接口地址"
            placeholder={format === 'openai' ? 'https://api.example.com/v1' : 'https://api.example.com'}
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
          <Input
            label="API key"
            type="password"
            autoComplete="off"
            placeholder={provider?.has_key ? `${provider.api_key}(不修改请留空)` : undefined}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          {format === 'anthropic' && (
            <Input label="anthropic-version" value={version} onChange={(e) => setVersion(e.target.value)} />
          )}
          <Checkbox label="OpenCode Go 适配" checked={openCodeGo ?? isOpenCodeGo(baseUrl)} onCheckedChange={(checked) => setOpenCodeGo(checked === true)} />
          <Textarea label="额外请求头" placeholder='{"X-Header": "value"}' rows={2} value={headers} onChange={(e) => setHeaders(e.target.value)} />
          {error && <Alert tone="danger" title="没有保存">{error}</Alert>}
          <Footer onClose={onClose} saving={saving} disabled={!name.trim() || !baseUrl.trim()} label={provider ? '保存修改' : '添加厂商'} onSave={save} />
        </div>
      </DialogContent>
    </Dialog>
  );
}

function useProviderModels(providerName: string | null) {
  const [state, setState] = useState<{ loading: boolean; models: ModelInfo[]; error: string }>({ loading: true, models: [], error: '' });
  useEffect(() => {
    if (!providerName) return;
    let cancelled = false;
    setState({ loading: true, models: [], error: '' });
    api<ModelInfo[]>(`/api/providers/${enc(providerName)}/models`)
      .then((models) => !cancelled && setState({ loading: false, models, error: '' }))
      .catch((e) => !cancelled && setState({ loading: false, models: [], error: errorText(e) }));
    return () => {
      cancelled = true;
    };
  }, [providerName]);
  return state;
}

export function ModelPicker({ provider, existing, onClose, onSaved }: { provider: Provider; existing: ModelProfile[]; onClose: () => void; onSaved: () => void }) {
  const { loading, models, error } = useProviderModels(provider.name);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState('');
  const [manual, setManual] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const added = new Set(existing.filter((m) => m.provider === provider.name).map((m) => m.model));
  const shown = useMemo(() => models.filter((m) => m.id.toLowerCase().includes(query.toLowerCase())), [models, query]);
  const ids = [...picked, ...manual.split(/[,，\s]+/).filter(Boolean)];

  async function save() {
    setSaving(true);
    setSaveError('');
    const names = new Set(existing.map((m) => m.name));
    const failed: string[] = [];
    for (const id of ids) {
      const name = names.has(id) ? `${provider.name}-${id}` : id;
      try {
        await api('/api/models', {
          method: 'POST',
          body: { name, provider: provider.name, model: id, max_tokens: null, reasoning_effort: null, extra_body: {}, extra_headers: {}, tool_mode: 'native', timeout_seconds: 300 },
        });
        names.add(name);
      } catch (e) {
        failed.push(`${id}:${errorText(e)}`);
      }
    }
    setSaving(false);
    if (failed.length) setSaveError(failed.join(';'));
    else onSaved();
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent title={`从 ${provider.name} 选择模型`}>
        <div className={styles.form}>
          {loading && <Skeleton lines={4} label="正在拉取模型列表" />}
          {error && <Alert tone="warning" title="拉取模型列表失败,请手动填写模型 id">{error}</Alert>}
          {models.length > 0 && (
            <>
              {models.length > 8 && <SearchField label="筛选模型" value={query} onValueChange={setQuery} />}
              <div className={styles.pickList}>
                {shown.map((m) => (
                  <div key={m.id} className={styles.pickRow}>
                    <Checkbox
                      label={m.id}
                      description={[m.display_name, m.context_window ? `上下文 ${m.context_window.toLocaleString('zh-CN')}` : '', added.has(m.id) ? '已添加' : '']
                        .filter(Boolean)
                        .join(' · ')}
                      checked={picked.has(m.id)}
                      disabled={added.has(m.id)}
                      onCheckedChange={(on) => {
                        const next = new Set(picked);
                        if (on === true) next.add(m.id);
                        else next.delete(m.id);
                        setPicked(next);
                      }}
                    />
                  </div>
                ))}
              </div>
            </>
          )}
          <Input label="模型 id" placeholder="model-a, model-b" value={manual} onChange={(e) => setManual(e.target.value)} />
          {saveError && <Alert tone="danger" title="有模型没有添加成功">{saveError}</Alert>}
          <Footer onClose={onClose} saving={saving} disabled={!ids.length} label={ids.length ? `添加 ${ids.length} 个模型` : '添加模型'} onSave={save} />
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** 下拉框不能用空字符串当选项值,「不设置」用这个代替 */
const VENDOR_DEFAULT = 'default';

export function ModelDialog({ model, providers, onClose, onSaved }: { model: ModelProfile | null; providers: Provider[]; onClose: () => void; onSaved: () => void }) {
  const [provider, setProvider] = useState(model?.provider ?? providers[0]?.name ?? '');
  const [modelId, setModelId] = useState(model?.model ?? '');
  const [name, setName] = useState(model?.name ?? '');
  const [maxTokens, setMaxTokens] = useState(model?.max_tokens ?? 0);
  const [toolMode, setToolMode] = useState<ModelProfile['tool_mode']>(model?.tool_mode ?? 'native');
  const [effort, setEffort] = useState<string>(model?.reasoning_effort ?? VENDOR_DEFAULT);
  const [timeoutS, setTimeoutS] = useState(model?.timeout_seconds ?? 300);
  const [extraBody, setExtraBody] = useState(pretty(model?.extra_body));
  const [extraHeaders, setExtraHeaders] = useState(pretty(model?.extra_headers));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const available = useProviderModels(provider || null);

  async function save() {
    setSaving(true);
    setError('');
    try {
      const body = {
        name: name.trim() || modelId.trim(),
        provider,
        model: modelId.trim(),
        max_tokens: maxTokens > 0 ? maxTokens : null,
        reasoning_effort: effort === VENDOR_DEFAULT ? null : (effort as Effort),
        extra_body: parseJsonObject(extraBody, '额外请求参数'),
        extra_headers: parseJsonObject(extraHeaders, '额外请求头'),
        tool_mode: toolMode,
        timeout_seconds: timeoutS,
      };
      if (model) await api(`/api/models/${enc(model.name)}`, { method: 'PUT', body });
      else await api('/api/models', { method: 'POST', body });
      onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent title={model ? `编辑 ${model.name}` : '添加模型档案'}>
        <div className={styles.form}>
          <Select label="厂商" options={providers.map((p) => ({ value: p.name, label: p.name }))} value={provider} onValueChange={setProvider} />
          {available.models.length > 0 ? (
            <Select
              label="模型"
              options={available.models.map((m) => ({ value: m.id, label: m.display_name ? `${m.id}(${m.display_name})` : m.id }))}
              value={modelId}
              placeholder="选择模型"
              onValueChange={setModelId}
            />
          ) : (
            <Input label="模型 id" value={modelId} onChange={(e) => setModelId(e.target.value)} />
          )}
          <Input label="档案名" placeholder={modelId || undefined} value={name} onChange={(e) => setName(e.target.value)} />
          <SegmentedControl
            label="工具调用方式"
            options={[
              { value: 'native', label: '原生工具调用' },
              { value: 'json', label: 'JSON 输出' },
            ]}
            value={toolMode}
            onValueChange={(v) => setToolMode(v as ModelProfile['tool_mode'])}
            fullWidth
          />
          <Select label="思考强度" options={[{ value: VENDOR_DEFAULT, label: '厂商默认' }, ...EFFORTS]} value={effort} onValueChange={setEffort} />
          <div className={page.fields2}>
            <NumberField label="max_tokens(0 为不限)" min={0} max={200000} step={1000} locale="zh-CN" formatOptions={{ useGrouping: false }} value={maxTokens} onValueChange={setMaxTokens} />
            <NumberField label="超时" suffix=" 秒" min={10} max={3600} step={30} value={timeoutS} onValueChange={setTimeoutS} />
          </div>
          <Textarea label="额外请求参数" placeholder='{"thinking": {"type": "enabled"}}' rows={4} value={extraBody} onChange={(e) => setExtraBody(e.target.value)} />
          <Textarea label="额外请求头" placeholder='{"X-Header": "value"}' rows={2} value={extraHeaders} onChange={(e) => setExtraHeaders(e.target.value)} />
          {error && <Alert tone="danger" title="没有保存">{error}</Alert>}
          <Footer onClose={onClose} saving={saving} disabled={!provider || !modelId.trim()} label={model ? '保存修改' : '添加模型档案'} onSave={save} />
        </div>
      </DialogContent>
    </Dialog>
  );
}
