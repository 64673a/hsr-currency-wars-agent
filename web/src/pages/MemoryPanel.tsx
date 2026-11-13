import { Alert, Badge, Button, Dialog, Input, List, ListItem, Option, Select, Textarea, Toggle } from '@arclux/arc-ui-react';
import { useCallback, useEffect, useState } from 'react';

import { api, CONFIDENCE, MEMORY_STATUS, type Memory } from '../api';
import { errorText, splitList, toast } from '../ui';

const STATUS_VARIANT: Record<Memory['status'], any> = { approved: 'success', draft: 'warning', rejected: 'error', archived: 'default' };

export default function MemoryPanel() {
  const [items, setItems] = useState<Memory[]>([]);
  const [filter, setFilter] = useState('');
  const [editing, setEditing] = useState<Memory | 'new' | null>(null);

  const reload = useCallback(async () => {
    try {
      setItems(await api<Memory[]>('/api/memories'));
    } catch (e) {
      toast(errorText(e), 'error');
    }
  }, []);
  useEffect(() => {
    reload();
  }, [reload]);

  async function setStatus(m: Memory, status: Memory['status']) {
    try {
      await api(`/api/memories/${m.id}/status`, { method: 'POST', body: { status } });
      reload();
    } catch (e) {
      toast(errorText(e), 'error');
    }
  }

  const shown = items.filter((m) => !filter || m.status === filter);
  return (
    <div>
      <p className="muted">
        积累的游戏经验(机制 / 打法 / 教训),决策时按局面里出现的名字、节点类型和界面自动想起。公共记忆在 knowledge/memories.yaml
        随仓库发布,本机学到的存在 data/memory.sqlite;在这里修改公共记忆会保存为本机版本。
      </p>
      <div className="row" style={{ margin: '8px 0 12px' }}>
        <Select value={filter} onArcChange={(e) => setFilter(e.detail.value)}>
          <Option value="">全部状态</Option>
          {Object.entries(MEMORY_STATUS).map(([k, v]) => (
            <Option key={k} value={k}>
              {v}
            </Option>
          ))}
        </Select>
        <Button size="sm" onClick={() => setEditing('new')}>
          添加记忆
        </Button>
      </div>
      {shown.length === 0 ? (
        <Alert variant="info">没有记忆。</Alert>
      ) : (
        <List variant="separated">
          {shown.map((m) => {
            const t = m.triggers;
            const trig = [...t.entities, ...t.nodes.map((n) => `节点:${n}`), ...t.screens.map((s) => `界面:${s}`)];
            const harmful = m.stats.hurt > m.stats.helped && m.stats.hurt >= 2;
            return (
              <ListItem key={m.id}>
                <span>
                  <Badge size="sm" variant={STATUS_VARIANT[m.status]}>
                    {MEMORY_STATUS[m.status]}
                  </Badge>{' '}
                  <Badge size="sm">{m.kind}</Badge> <b>{m.title}</b>
                </span>
                <span slot="description">
                  <span className="desc">{m.content}</span>
                  <br />
                  <span className="muted">
                    触发:{trig.join('、') || '通用'} · {CONFIDENCE[m.confidence]} · {m.origin} · 来源 {m.source || '—'} · 注入 {m.stats.injected} 次 ·
                    有帮助 {m.stats.helped} / 有害 {m.stats.hurt}
                  </span>
                  {harmful && <Alert variant="error" density="compact">有害票多于有帮助票,建议修改或归档</Alert>}
                </span>
                <span slot="actions" className="list-actions">
                  {m.status !== 'approved' && (
                    <Button size="sm" variant="secondary" onClick={() => setStatus(m, 'approved')}>
                      采纳
                    </Button>
                  )}
                  {m.status === 'draft' && (
                    <Button size="sm" variant="ghost" onClick={() => setStatus(m, 'rejected')}>
                      驳回
                    </Button>
                  )}
                  {m.status === 'approved' && (
                    <Button size="sm" variant="ghost" onClick={() => setStatus(m, 'archived')}>
                      归档
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => setEditing(m)}>
                    编辑
                  </Button>
                </span>
              </ListItem>
            );
          })}
        </List>
      )}
      <PreviewBox />
      {editing && (
        <MemoryDialog
          memory={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}
    </div>
  );
}

function PreviewBox() {
  const [entities, setEntities] = useState('经济严重过热, 绯英');
  const [nodes, setNodes] = useState('reward');
  const [layer, setLayer] = useState('战术');
  const [draft, setDraft] = useState(false);
  const [result, setResult] = useState<{ items: { id: string; title: string; score: number; reasons: string[] }[]; prompt: string } | null>(null);

  async function run() {
    try {
      setResult(
        await api('/api/memories/preview', {
          method: 'POST',
          body: { entities: splitList(entities), nodes: splitList(nodes), layer: layer || null, include_draft: draft },
        }),
      );
    } catch (e) {
      toast(errorText(e), 'error');
    }
  }

  return (
    <div className="section">
      <h2>检索预览</h2>
      <p className="muted">模拟一个决策点的局面线索,看会想起哪些记忆、注入提示词的文字是什么样。</p>
      <div className="grid2">
        <Input label="局面里的名字(逗号分隔)" value={entities} onArcInput={(e) => setEntities(e.detail.value)} />
        <Input label="节点类型(如 reward、battle)" value={nodes} onArcInput={(e) => setNodes(e.detail.value)} />
        <Select label="决策层" value={layer} onArcChange={(e) => setLayer(e.detail.value)}>
          <Option value="">不限</Option>
          {['战略', '战术', '选择', '事件'].map((l) => (
            <Option key={l} value={l}>
              {l}
            </Option>
          ))}
        </Select>
        <Toggle label="包含待审核草稿" checked={draft} onArcChange={(e) => setDraft(e.detail.checked)} />
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <Button size="sm" onClick={run}>
          预览
        </Button>
      </div>
      {result && (
        <>
          {result.items.map((r) => (
            <div key={r.id} className="muted">
              {r.score.toFixed(2)} · {r.title} ← {r.reasons.join('、')}
            </div>
          ))}
          <pre className="block">{result.prompt || '(没有相关记忆)'}</pre>
        </>
      )}
    </div>
  );
}

function MemoryDialog({ memory, onClose, onSaved }: { memory: Memory | null; onClose: () => void; onSaved: () => void }) {
  const [kind, setKind] = useState<Memory['kind']>(memory?.kind ?? '打法');
  const [title, setTitle] = useState(memory?.title ?? '');
  const [content, setContent] = useState(memory?.content ?? '');
  const [entities, setEntities] = useState(memory?.triggers.entities.join(', ') ?? '');
  const [nodes, setNodes] = useState(memory?.triggers.nodes.join(', ') ?? '');
  const [screens, setScreens] = useState(memory?.triggers.screens.join(', ') ?? '');
  const [scope, setScope] = useState(memory?.scope.join(', ') ?? '');
  const [confidence, setConfidence] = useState<Memory['confidence']>(memory?.confidence ?? 'medium');
  const [source, setSource] = useState(memory?.source ?? '维护者');
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      const body = {
        ...(memory ?? { status: 'approved', evidence: [] }),
        kind,
        title: title.trim(),
        content: content.trim(),
        triggers: { entities: splitList(entities), nodes: splitList(nodes), screens: splitList(screens) },
        scope: splitList(scope),
        confidence,
        source,
      };
      if (memory) await api(`/api/memories/${memory.id}`, { method: 'PUT', body });
      else await api('/api/memories', { method: 'POST', body });
      toast('已保存记忆', 'success');
      onSaved();
    } catch (e) {
      toast(errorText(e), 'error');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open heading={memory ? '编辑记忆' : '添加记忆'} size="lg" onArcClose={onClose}>
      <div className="form">
        <div className="grid2">
          <Select label="类别" value={kind} onArcChange={(e) => setKind(e.detail.value)}>
            {['机制', '打法', '教训'].map((k) => (
              <Option key={k} value={k}>
                {k}
              </Option>
            ))}
          </Select>
          <Select label="置信度" value={confidence} onArcChange={(e) => setConfidence(e.detail.value)}>
            {Object.entries(CONFIDENCE).map(([k, v]) => (
              <Option key={k} value={k}>
                {v}
              </Option>
            ))}
          </Select>
        </div>
        <Input label="标题(一句话)" value={title} onArcInput={(e) => setTitle(e.detail.value)} />
        <Textarea label="内容:条件、做法、原因" rows={5} value={content} onArcInput={(e) => setContent(e.detail.value)} />
        <Input label="触发名字(逗号分隔)" value={entities} onArcInput={(e) => setEntities(e.detail.value)} />
        <div className="grid2">
          <Input label="触发节点类型" value={nodes} onArcInput={(e) => setNodes(e.detail.value)} />
          <Input label="触发界面" value={screens} onArcInput={(e) => setScreens(e.detail.value)} />
        </div>
        <div className="grid2">
          <Input label="限定决策层(战略/战术/选择/事件,空为不限)" value={scope} onArcInput={(e) => setScope(e.detail.value)} />
          <Input label="来源" value={source} onArcInput={(e) => setSource(e.detail.value)} />
        </div>
      </div>
      <div slot="footer" className="row">
        <Button variant="ghost" onClick={onClose}>
          取消
        </Button>
        <Button loading={saving} disabled={!title.trim() || !content.trim()} onClick={save}>
          保存
        </Button>
      </div>
    </Dialog>
  );
}
