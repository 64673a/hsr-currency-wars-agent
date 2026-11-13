import { useCallback, useEffect, useId, useState } from 'react';

import { Alert } from '@/components/arc/alert/alert';
import { Badge } from '@/components/arc/badge/badge';
import { Button } from '@/components/arc/button/button';
import { ConfirmMorph } from '@/components/arc/confirm-morph/confirm-morph';
import { Input } from '@/components/arc/input/input';
import { NumberField } from '@/components/arc/number-field/number-field';
import { Select } from '@/components/arc/select/select';
import { Skeleton } from '@/components/arc/skeleton/skeleton';
import { Switch } from '@/components/arc/switch/switch';
import { api, enc, errorText, type ModelProfile, type Provider, type RoleKey, type Settings, type TestResult } from '@/lib/api';
import page from '@/lib/page.module.css';

import { ModelDialog, ModelPicker, ProviderDialog } from './dialogs';

const ROLES: { key: RoleKey; label: string; hint: string }[] = [
  { key: 'strategic', label: '战略层', hint: '开局制定对局方向,遇到会改变流程的修正时重新规划' },
  { key: 'tactical', label: '战术层', hint: '每个备战阶段决定买卖、站位、装备、升级和刷新' },
  { key: 'chooser', label: '选择类', hint: '投资环境、投资策略、主角光环等三选一' },
  { key: 'review', label: '复盘', hint: '每局结束后总结经验,写入记忆草稿' },
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
        <div>
          <h1 className={page.title}>设置</h1>
          <p className={page.subtitle}>配置保存在项目根目录的 config.yaml,不会提交到仓库</p>
        </div>
      </header>

      {loadError && (
        <Alert tone="danger" title="读取配置失败">
          {loadError}。确认后端已启动后{' '}
          <Button size="sm" variant="secondary" onClick={reload}>
            重新读取
          </Button>
        </Alert>
      )}

      <section className={page.section} aria-labelledby="providers">
        <div className={page.sectionHead}>
          <div>
            <h2 id="providers" className={page.heading}>厂商</h2>
            <p className={page.hint}>接口地址和 key 只填一次,模型从厂商的模型列表里挑选。兼容 OpenAI 或 Anthropic 格式的接口都可以接入。</p>
          </div>
          <Button size="sm" variant="secondary" onClick={() => setEditProvider('new')}>
            添加厂商
          </Button>
        </div>
        {providers === null ? (
          <Skeleton lines={2} />
        ) : providers.length === 0 ? (
          <Alert tone="info" title="还没有厂商">先添加一个厂商,再从它的模型列表里选模型。</Alert>
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
                  <ConfirmMorph
                    label="删除"
                    prompt={p.models?.length ? '先删除它的模型档案' : `删除 ${p.name}?`}
                    confirmLabel="删除"
                    cancelLabel="取消"
                    pendingLabel="删除中"
                    doneLabel="已删除"
                    errorLabel="删除失败"
                    retryLabel="重试"
                    disabled={!!p.models?.length}
                    onConfirm={async () => {
                      await api(`/api/providers/${enc(p.name)}`, { method: 'DELETE' });
                      await reload();
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className={page.section} aria-labelledby="models">
        <div className={page.sectionHead}>
          <div>
            <h2 id="models" className={page.heading}>模型档案</h2>
            <p className={page.hint}>同一厂商可以建多个档案,用来对比不同模型,或同一模型的不同参数。</p>
          </div>
          <Button size="sm" variant="secondary" disabled={!providers?.length} onClick={() => setEditModel('new')}>
            添加模型档案
          </Button>
        </div>
        {providers !== null && models.length === 0 ? (
          <Alert tone="info" title="还没有模型档案">在厂商一栏点击「选择模型」,勾选要用的模型。</Alert>
        ) : (
          <div className={page.group}>
            {models.map((m) => (
              <ModelRow key={m.name} model={m} onEdit={() => setEditModel(m)} onDeleted={reload} />
            ))}
          </div>
        )}
      </section>

      {settings && <RolesSection settings={settings} models={models} onSaved={setSettings} />}
      {settings && <ParamsSection settings={settings} onSaved={setSettings} />}

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
        {result.latency_s}s · 工具调用{result.tool_call ? '正常' : '缺失'} · 思考内容{result.reasoning ? '有' : '本次未返回'}
        {result.usage && ` · token ${result.usage.input_tokens} / ${result.usage.output_tokens}`}
      </span>
    </span>
  );
}

function useSave(onSaved: (s: Settings) => void) {
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [error, setError] = useState('');
  async function save(next: Settings) {
    setState('saving');
    try {
      onSaved(await api<Settings>('/api/config', { method: 'PUT', body: next }));
      setState('saved');
    } catch (e) {
      setError(errorText(e));
      setState('error');
    }
  }
  return { state, error, save, reset: () => setState('idle') };
}

function RolesSection({ settings, models, onSaved }: { settings: Settings; models: ModelProfile[]; onSaved: (s: Settings) => void }) {
  const id = useId();
  const [roles, setRoles] = useState(settings.roles);
  const { state, error, save, reset } = useSave(onSaved);
  const dirty = JSON.stringify(roles) !== JSON.stringify(settings.roles);
  const options = [{ value: '__default', label: `默认(${models[0]?.name ?? '无'})` }, ...models.map((m) => ({ value: m.name, label: m.name }))];

  return (
    <section className={page.section} aria-labelledby={`${id}-roles`}>
      <div>
        <h2 id={`${id}-roles`} className={page.heading}>决策层使用的模型</h2>
        <p className={page.hint}>不同层可以用不同模型,例如战略层用更强的模型、战术层用更快的模型。</p>
      </div>
      <div className={page.fields2}>
        {ROLES.map((r) => (
          <Select
            key={r.key}
            label={r.label}
            description={r.hint}
            options={options}
            value={roles[r.key] ?? '__default'}
            onValueChange={(v) => {
              setRoles({ ...roles, [r.key]: v === '__default' ? null : v });
              reset();
            }}
          />
        ))}
      </div>
      {state === 'error' && <Alert tone="danger" title="没有保存">{error}</Alert>}
      <div className={page.actions}>
        <Button size="sm" variant="secondary" loading={state === 'saving'} disabled={!dirty && state !== 'error'} onClick={() => save({ ...settings, roles })}>
          {state === 'saved' && !dirty ? '已保存' : '保存决策层设置'}
        </Button>
      </div>
    </section>
  );
}

function ParamsSection({ settings, onSaved }: { settings: Settings; onSaved: (s: Settings) => void }) {
  const id = useId();
  const [s, setS] = useState(settings);
  const { state, error, save, reset } = useSave(onSaved);
  const dirty = JSON.stringify(s) !== JSON.stringify(settings);
  const update = (next: Settings) => {
    setS(next);
    reset();
  };

  return (
    <section className={page.section} aria-labelledby={`${id}-params`}>
      <h2 id={`${id}-params`} className={page.heading}>运行参数</h2>
      <div className={page.group}>
        <div className={page.row}>
          <div className={page.rowText}>
            <span id={`${id}-guides`} className={page.rowTitle}>给模型看官方阵容指引卡</span>
            <p className={page.rowHint}>关闭后模型只靠规则和实体描述自己判断,用来比较模型的真实水平</p>
          </div>
          <Switch aria-labelledby={`${id}-guides`} checked={s.agent.use_guides} onCheckedChange={(on) => update({ ...s, agent: { ...s.agent, use_guides: on } })} />
        </div>
      </div>
      <div className={page.fields2}>
        <NumberField
          label="每个备战阶段最多调用战术层"
          suffix=" 次"
          min={1}
          max={20}
          value={s.agent.max_tactical_calls_per_stage}
          onValueChange={(v) => update({ ...s, agent: { ...s.agent, max_tactical_calls_per_stage: v } })}
        />
        <NumberField
          label="单局时长上限"
          suffix=" 分钟"
          min={10}
          max={600}
          step={10}
          value={s.agent.max_run_minutes}
          onValueChange={(v) => update({ ...s, agent: { ...s.agent, max_run_minutes: v } })}
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
      </div>
      <div className={page.fields2}>
        <Input
          label="网页监听地址"
          description="改成 0.0.0.0 才能从局域网其他设备打开,重启后生效"
          value={s.server.host}
          onChange={(e) => update({ ...s, server: { ...s.server, host: e.target.value } })}
        />
        <NumberField
          label="网页端口"
          description="重启后生效"
          min={1024}
          max={65535}
          locale="zh-CN"
          formatOptions={{ useGrouping: false }}
          value={s.server.port}
          onValueChange={(v) => update({ ...s, server: { ...s.server, port: v } })}
        />
      </div>
      {s.server.host !== '127.0.0.1' && s.server.host !== 'localhost' && (
        <Alert tone="warning" title="局域网内的设备都能访问">网页没有登录机制,同一网络里的任何设备都能查看并控制 agent。</Alert>
      )}
      {state === 'error' && <Alert tone="danger" title="没有保存">{error}</Alert>}
      <div className={page.actions}>
        <Button loading={state === 'saving'} disabled={!dirty && state !== 'error'} onClick={() => save(s)}>
          {state === 'saved' && !dirty ? '已保存' : '保存运行参数'}
        </Button>
      </div>
    </section>
  );
}
