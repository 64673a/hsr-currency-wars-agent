import { useCallback, useEffect, useId, useState } from 'react';

import { Alert } from '@/components/arc/alert/alert';
import { Badge } from '@/components/arc/badge/badge';
import { Button } from '@/components/arc/button/button';
import { ConfirmMorph } from '@/components/arc/confirm-morph/confirm-morph';
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog';
import { Input } from '@/components/arc/input/input';
import { NumberField } from '@/components/arc/number-field/number-field';
import SegmentedControl from '@/components/arc/segmented-control/segmented-control';
import { Select } from '@/components/arc/select/select';
import { Skeleton } from '@/components/arc/skeleton/skeleton';
import { Switch } from '@/components/arc/switch/switch';
import {
  api,
  effortName,
  enc,
  errorText,
  type ModelProfile,
  type OcrInfo,
  type OcrSettings,
  type OcrTest,
  type Provider,
  type RoleKey,
  type Settings,
  type TestResult,
} from '@/lib/api';
import page from '@/lib/page.module.css';

import { ModelDialog, ModelPicker, ProviderDialog } from './dialogs';

const ROLES: { key: RoleKey; label: string }[] = [
  { key: 'strategic', label: '战略' },
  { key: 'tactical', label: '备战' },
  { key: 'chooser', label: '三选一' },
  { key: 'navigator', label: '导航' },
  { key: 'review', label: '复盘' },
];

export default function SettingsPage() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loadError, setLoadError] = useState('');
  const [editProvider, setEditProvider] = useState<Provider | 'new' | null>(null);
  const [editModel, setEditModel] = useState<ModelProfile | 'new' | null>(null);
  const [picker, setPicker] = useState<Provider | null>(null);

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
      setLoadError('');
    } catch (e) {
      setLoadError(errorText(e));
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  return (
    <div className={`${page.page} ${page.narrow}`}>
      <header className={page.header}>
        <h1 className={page.title}>设置</h1>
      </header>

      {loadError && (
        <Alert tone="danger" title="读取配置失败">
          {loadError}{' '}
          <Button size="sm" variant="secondary" onClick={reload}>
            重试
          </Button>
        </Alert>
      )}

      <section className={page.section} aria-labelledby="providers">
        <div className={page.sectionHead}>
          <h2 id="providers" className={page.heading}>厂商</h2>
          <Button size="sm" variant="secondary" onClick={() => setEditProvider('new')}>
            添加厂商
          </Button>
        </div>
        {providers === null ? (
          <Skeleton lines={2} />
        ) : providers.length === 0 ? (
          <Alert tone="info" title="还没有厂商" />
        ) : (
          <div className={page.group}>
            {providers.map((p) => (
              <div key={p.name} className={page.row}>
                <div className={page.rowText}>
                  <span className={page.rowTitle}>
                    {p.name}
                    <Badge size="sm" tone="info">{p.format === 'openai' ? 'OpenAI 格式' : 'Anthropic 格式'}</Badge>
                  </span>
                  <p className={page.rowHint}>
                    {p.base_url} · {p.has_key ? `key ${p.api_key}` : '未填 key'}
                  </p>
                  <p className={page.rowHint}>模型档案:{p.models?.length ? p.models.join('、') : '无'}</p>
                </div>
                <div className={page.rowActions}>
                  <Button size="sm" variant="secondary" onClick={() => setPicker(p)}>
                    选择模型
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditProvider(p)}>
                    编辑
                  </Button>
                  {!p.models?.length && (
                    <ConfirmMorph
                      label="删除"
                      prompt={`删除 ${p.name}?`}
                      confirmLabel="删除"
                      cancelLabel="取消"
                      pendingLabel="删除中"
                      doneLabel="已删除"
                      errorLabel="删除失败"
                      retryLabel="重试"
                      onConfirm={async () => {
                        await api(`/api/providers/${enc(p.name)}`, { method: 'DELETE' });
                        await reload();
                      }}
                    />
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className={page.section} aria-labelledby="models">
        <div className={page.sectionHead}>
          <h2 id="models" className={page.heading}>模型档案</h2>
          {!!providers?.length && (
            <Button size="sm" variant="secondary" onClick={() => setEditModel('new')}>
              添加模型档案
            </Button>
          )}
        </div>
        {providers === null ? (
          <Skeleton lines={2} />
        ) : models.length === 0 ? (
          <Alert tone="info" title="还没有模型档案" />
        ) : (
          <div className={page.group}>
            {models.map((m) => (
              <ModelRow key={m.name} model={m} onEdit={() => setEditModel(m)} onDeleted={reload} />
            ))}
          </div>
        )}
      </section>

      {settings && <ConfigForm settings={settings} models={models} onSaved={setSettings} />}

      {editProvider && (
        <ProviderDialog
          provider={editProvider === 'new' ? null : editProvider}
          onClose={() => setEditProvider(null)}
          onSaved={async (created) => {
            setEditProvider(null);
            await reload();
            if (created) setPicker(created);
          }}
        />
      )}
      {editModel && providers && (
        <ModelDialog
          model={editModel === 'new' ? null : editModel}
          providers={providers}
          onClose={() => setEditModel(null)}
          onSaved={async () => {
            setEditModel(null);
            await reload();
          }}
        />
      )}
      {picker && (
        <ModelPicker
          provider={picker}
          existing={models}
          onClose={() => setPicker(null)}
          onSaved={async () => {
            setPicker(null);
            await reload();
          }}
        />
      )}
    </div>
  );
}

function ModelRow({ model: m, onEdit, onDeleted }: { model: ModelProfile; onEdit: () => void; onDeleted: () => Promise<void> }) {
  const [test, setTest] = useState<TestResult | 'running' | null>(null);
  const extra = Object.keys(m.extra_body).length ? JSON.stringify(m.extra_body) : '';

  async function runTest() {
    setTest('running');
    try {
      setTest(await api<TestResult>(`/api/models/${enc(m.name)}/test`, { method: 'POST' }));
    } catch (e) {
      setTest({ ok: false, error: errorText(e) });
    }
  }

  return (
    <div className={page.row}>
      <div className={page.rowText}>
        <span className={page.rowTitle}>
          {m.name}
          {m.tool_mode === 'json' && <Badge size="sm">JSON 模式</Badge>}
        </span>
        <p className={page.rowHint}>
          {m.provider} / {m.model}
          {m.reasoning_effort && ` · 思考强度:${effortName(m.reasoning_effort)}`}
        </p>
        {extra && <span className={page.mono}>{extra}</span>}
        {test && test !== 'running' && <TestLine result={test} />}
      </div>
      <div className={page.rowActions}>
        <Button size="sm" variant="secondary" loading={test === 'running'} onClick={runTest}>
          {test && test !== 'running' ? '再测一次' : '测试连接'}
        </Button>
        <Button size="sm" variant="ghost" onClick={onEdit}>
          编辑
        </Button>
        <ConfirmMorph
          label="删除"
          prompt={`删除 ${m.name}?`}
          confirmLabel="删除"
          cancelLabel="取消"
          pendingLabel="删除中"
          doneLabel="已删除"
          errorLabel="删除失败"
          retryLabel="重试"
          onConfirm={async () => {
            await api(`/api/models/${enc(m.name)}`, { method: 'DELETE' });
            await onDeleted();
          }}
        />
      </div>
    </div>
  );
}

function TestLine({ result }: { result: TestResult }) {
  if (!result.ok)
    return (
      <span className={page.rowTitle}>
        <Badge size="sm" tone="danger" dot>
          不可用
        </Badge>
        <span className={page.rowHint}>{result.error}</span>
      </span>
    );
  return (
    <span className={page.rowTitle}>
      <Badge size="sm" tone="success" dot>
        可用
      </Badge>
      <span className={page.rowHint}>
        {result.latency_s}s · 读图{result.vision ? '正常' : '未通过'} · 工具调用{result.tool_call ? '正常' : '缺失'} · 思考内容{result.reasoning ? '有' : '本次未返回'}
        {result.usage && ` · token ${result.usage.input_tokens} / ${result.usage.output_tokens}`}
      </span>
    </span>
  );
}

// 各环节的模型、运行参数和网页服务同属一份配置,一起编辑、一起保存
function ConfigForm({ settings, models, onSaved }: { settings: Settings; models: ModelProfile[]; onSaved: (s: Settings) => void }) {
  const id = useId();
  const [s, setS] = useState(settings);
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [error, setError] = useState('');
  const [confirmLan, setConfirmLan] = useState(false);
  const [serverChanged, setServerChanged] = useState(false);
  const dirty = JSON.stringify(s) !== JSON.stringify(settings);
  const update = (next: Settings) => {
    setS(next);
    setState('idle');
  };
  const agent = (patch: Partial<Settings['agent']>) => update({ ...s, agent: { ...s.agent, ...patch } });
  const options = [{ value: '__default', label: `默认(${models[0]?.name ?? '无'})` }, ...models.map((m) => ({ value: m.name, label: m.name }))];
  const exposes = (x: Settings) => !['127.0.0.1', 'localhost'].includes(x.server.host.trim());

  async function commit() {
    setConfirmLan(false);
    setServerChanged(JSON.stringify(s.server) !== JSON.stringify(settings.server));
    setState('saving');
    try {
      const saved = await api<Settings>('/api/config', { method: 'PUT', body: s });
      onSaved(saved);
      setS(saved);
      setState('saved');
    } catch (e) {
      setError(errorText(e));
      setState('error');
    }
  }

  return (
    <>
      <section className={page.section} aria-labelledby={`${id}-roles`}>
        <h2 id={`${id}-roles`} className={page.heading}>各环节使用的模型</h2>
        <div className={page.fields2}>
          {ROLES.map((r) => (
            <Select
              key={r.key}
              label={r.label}
              options={options}
              value={s.roles[r.key] ?? '__default'}
              onValueChange={(v) => update({ ...s, roles: { ...s.roles, [r.key]: v === '__default' ? null : v } })}
            />
          ))}
        </div>
      </section>

      <section className={page.section} aria-labelledby={`${id}-params`}>
        <h2 id={`${id}-params`} className={page.heading}>运行参数</h2>
        <div className={page.fields2}>
          <NumberField
            label="每个备战阶段最多决策"
            suffix=" 次"
            min={1}
            max={20}
            value={s.agent.max_tactical_calls_per_stage}
            onValueChange={(v) => agent({ max_tactical_calls_per_stage: v })}
          />
          <NumberField
            label="单局时长上限"
            suffix=" 分钟"
            min={10}
            max={600}
            step={10}
            value={s.agent.max_run_minutes}
            onValueChange={(v) => agent({ max_run_minutes: v })}
          />
          <NumberField
            label="知识同步抓取的阵容数"
            suffix=" 套"
            min={0}
            max={2000}
            step={50}
            value={s.knowledge.max_lineups}
            onValueChange={(v) => update({ ...s, knowledge: { ...s.knowledge, max_lineups: v } })}
          />
          <Input
            label="开拓者的名字"
            placeholder="游戏里开拓者显示的名字"
            value={s.agent.trailblazer_name}
            onChange={(e) => agent({ trailblazer_name: e.target.value })}
          />
          <div className={page.field}>
            <span id={`${id}-guides`} className={page.fieldLabel}>允许查攻略</span>
            <div className={page.switchControl}>
              <Switch aria-labelledby={`${id}-guides`} checked={s.agent.use_guides} onCheckedChange={(on) => agent({ use_guides: on })} />
            </div>
          </div>
        </div>
      </section>

      <OcrFields value={s.ocr} onChange={(ocr) => update({ ...s, ocr })} />

      <section className={page.section} aria-labelledby={`${id}-server`}>
        <h2 id={`${id}-server`} className={page.heading}>网页服务</h2>
        <div className={page.fields2}>
          <Input label="监听地址" value={s.server.host} onChange={(e) => update({ ...s, server: { ...s.server, host: e.target.value } })} />
          <NumberField
            label="端口"
            min={1024}
            max={65535}
            locale="zh-CN"
            formatOptions={{ useGrouping: false }}
            value={s.server.port}
            onValueChange={(v) => update({ ...s, server: { ...s.server, port: v } })}
          />
        </div>
        {state === 'error' && <Alert tone="danger" title="没有保存">{error}</Alert>}
        <div className={page.actions}>
          <Button loading={state === 'saving'} disabled={!dirty && state !== 'error'} onClick={() => (exposes(s) && !exposes(settings) ? setConfirmLan(true) : commit())}>
            {state === 'saved' && !dirty ? (serverChanged ? '已保存,重启后生效' : '已保存') : '保存'}
          </Button>
        </div>
      </section>

      <Dialog open={confirmLan} onOpenChange={setConfirmLan}>
        <DialogContent title="允许局域网访问?" description="同一网络里的任何设备都能打开网页并控制 agent。">
          <div className={page.actions}>
            <Button variant="ghost" onClick={() => setConfirmLan(false)}>
              取消
            </Button>
            <Button onClick={commit}>允许并保存</Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

const DEVICES = [
  { value: 'gpu', label: '显卡' },
  { value: 'cpu', label: 'CPU' },
];

// 文字识别用的设备:显卡(选哪块)或 CPU;测速按表单里还没保存的设置跑一张测试画面
function OcrFields({ value, onChange }: { value: OcrSettings; onChange: (v: OcrSettings) => void }) {
  const id = useId();
  const [info, setInfo] = useState<OcrInfo | null>(null);
  const [test, setTest] = useState<OcrTest | null>(null);
  const [testError, setTestError] = useState('');
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    api<OcrInfo>('/api/ocr')
      .then(setInfo)
      .catch(() => setInfo({ adapters: [], auto: null }));
  }, []);

  const cards = info?.adapters ?? [];
  const chosen = cards.some((a) => a.name === value.adapter) ? value.adapter! : 'auto';
  const gb = (mb: number) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`);
  const change = (patch: Partial<OcrSettings>) => {
    onChange({ ...value, ...patch });
    setTest(null);
    setTestError('');
  };

  async function runTest() {
    setTesting(true);
    setTestError('');
    try {
      setTest(await api<OcrTest>('/api/ocr/test', { method: 'POST', body: value }));
    } catch (e) {
      setTest(null);
      setTestError(errorText(e));
    } finally {
      setTesting(false);
    }
  }

  const fellBack = test && value.device === 'gpu' && cards.length > 0 && test.backend === 'CPU';
  return (
    <section className={page.section} aria-labelledby={`${id}-ocr`}>
      <div className={page.sectionHead}>
        <h2 id={`${id}-ocr`} className={page.heading}>文字识别</h2>
        <Button size="sm" variant="secondary" loading={testing} onClick={runTest}>
          测速
        </Button>
      </div>
      {cards.length > 0 && (
        <div className={page.fields2}>
          <div className={page.field}>
            <span id={`${id}-device`} className={page.fieldLabel}>识别设备</span>
            <SegmentedControl label="识别设备" options={DEVICES} value={value.device} onValueChange={(v) => change({ device: v as OcrSettings['device'] })} fullWidth />
          </div>
          {value.device === 'gpu' && (
            <Select
              label="显卡"
              options={[
                { value: 'auto', label: `自动(${info?.auto ?? '专用显存最大的'})` },
                ...cards.map((a) => ({ value: a.name, label: `${a.name} · ${gb(a.memory_mb)}` })),
              ]}
              value={chosen}
              onValueChange={(v) => change({ adapter: v === 'auto' ? null : v })}
            />
          )}
        </div>
      )}
      {test && (
        <span className={page.rowTitle}>
          <Badge size="sm" tone={fellBack ? 'warning' : 'success'} dot>
            {fellBack ? '没用上显卡' : '可用'}
          </Badge>
          <span className={page.rowHint}>
            {test.backend} · 整屏识别一次 {test.ms} 毫秒
          </span>
        </span>
      )}
      {testError && <Alert tone="danger" title="测速失败">{testError}</Alert>}
    </section>
  );
}
