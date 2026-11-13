import { useCallback, useEffect, useId, useState } from 'react';

import { Alert } from '@/components/arc/alert/alert';
import { Badge } from '@/components/arc/badge/badge';
import { Button } from '@/components/arc/button/button';
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog';
import EmptyState from '@/components/arc/empty-state/empty-state';
import { Input } from '@/components/arc/input/input';
import SegmentedControl from '@/components/arc/segmented-control/segmented-control';
import { Select } from '@/components/arc/select/select';
import { Skeleton } from '@/components/arc/skeleton/skeleton';
import { Switch } from '@/components/arc/switch/switch';
import { Textarea } from '@/components/arc/textarea/textarea';
import { api, CONFIDENCE, errorText, MEMORY_STATUS, splitList, type Memory } from '@/lib/api';
import page from '@/lib/page.module.css';

import styles from './knowledge.module.css';

const STATUS_TONE: Record<Memory['status'], 'success' | 'warning' | 'danger' | 'neutral'> = {
  approved: 'success',
  draft: 'warning',
  rejected: 'danger',
  archived: 'neutral',
};
const FILTERS = [
  { value: 'all', label: '全部' },
  { value: 'draft', label: '待审核' },
  { value: 'approved', label: '已采纳' },
  { value: 'archived', label: '已归档' },
];

export default function MemoryPanel() {
  const [items, setItems] = useState<Memory[] | null>(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('all');
  const [editing, setEditing] = useState<Memory | 'new' | null>(null);
  const [busy, setBusy] = useState('');

  const reload = useCallback(async () => {
    try {
      setItems(await api<Memory[]>('/api/memories'));
      setError('');
    } catch (e) {
      setError(errorText(e));
    }
  }, []);
  useEffect(() => {
    reload();
  }, [reload]);

  async function setStatus(m: Memory, status: Memory['status']) {
    setBusy(m.id + status);
    try {
      await api(`/api/memories/${m.id}/status`, { method: 'POST', body: { status } });
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy('');
    }
  }

  const shown = (items ?? []).filter((m) => filter === 'all' || m.status === filter);
  return (
    <div className={page.section} style={{ marginTop: 16 }}>
      <div className={page.sectionHead}>
        <p className={page.hint} style={{ maxWidth: 640 }}>
          积累的游戏经验:机制、打法和教训。决策时按局面里出现的名字、节点类型和界面自动想起。公共记忆随仓库发布,对局中学到的先作为草稿,审核后才会使用。
        </p>
        <Button size="sm" variant="secondary" onClick={() => setEditing('new')}>
          添加记忆
        </Button>
      </div>
      <SegmentedControl label="按状态筛选" options={FILTERS} value={filter} onValueChange={setFilter} />
      {error && <Alert tone="danger" title="读取失败">{error}</Alert>}
      {items === null ? (
        <Skeleton lines={5} />
      ) : shown.length === 0 ? (
        <EmptyState title="这里还没有记忆" description={filter === 'draft' ? '对局中模型写下的笔记和复盘结论会出现在这里等待审核。' : '添加一条你知道的游戏经验,agent 会在相关局面里想起它。'} />
      ) : (
        <div className={page.group}>
          {shown.map((m) => {
            const t = m.triggers;
            const trig = [...t.entities, ...t.nodes.map((n) => `节点 ${n}`), ...t.screens.map((s) => `界面 ${s}`)];
            const harmful = m.stats.hurt > m.stats.helped && m.stats.hurt >= 2;
            return (
              <div key={m.id} className={page.row}>
                <div className={page.rowText}>
                  <span className={page.rowTitle}>
                    {m.title}
                    <Badge size="sm" tone={STATUS_TONE[m.status]} dot>
                      {MEMORY_STATUS[m.status]}
                    </Badge>
                    <Badge size="sm">{m.kind}</Badge>
                    {harmful && (
                      <Badge size="sm" tone="danger">
                        有害票偏多
                      </Badge>
                    )}
                  </span>
                  <p className={styles.memoryContent}>{m.content}</p>
                  <p className={page.rowHint}>
                    想起时机:{trig.join('、') || '所有决策'} · {CONFIDENCE[m.confidence]} · {m.origin === '公共' ? '公共记忆' : '本机记忆'} · 用过 {m.stats.injected} 次
                  </p>
                </div>
                <div className={page.rowActions}>
                  {m.status === 'draft' && (
                    <>
                      <Button size="sm" variant="secondary" loading={busy === m.id + 'approved'} onClick={() => setStatus(m, 'approved')}>
                        采纳
                      </Button>
                      <Button size="sm" variant="ghost" loading={busy === m.id + 'rejected'} onClick={() => setStatus(m, 'rejected')}>
                        驳回
                      </Button>
                    </>
                  )}
                  {m.status === 'approved' && (
                    <Button size="sm" variant="ghost" loading={busy === m.id + 'archived'} onClick={() => setStatus(m, 'archived')}>
                      归档
                    </Button>
                  )}
                  {(m.status === 'archived' || m.status === 'rejected') && (
                    <Button size="sm" variant="ghost" loading={busy === m.id + 'approved'} onClick={() => setStatus(m, 'approved')}>
                      恢复
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => setEditing(m)}>
                    编辑
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <PreviewSection />
      {editing && (
        <MemoryDialog
          memory={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await reload();
          }}
        />
      )}
    </div>
  );
}

function PreviewSection() {
  const id = useId();
  const [entities, setEntities] = useState('经济严重过热, 绯英');
  const [nodes, setNodes] = useState('reward');
  const [layer, setLayer] = useState('战术');
  const [draft, setDraft] = useState(false);
  const [result, setResult] = useState<{ items: { id: string; title: string; score: number; reasons: string[] }[]; prompt: string } | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    setLoading(true);
    try {
      setResult(
        await api('/api/memories/preview', {
          method: 'POST',
          body: { entities: splitList(entities), nodes: splitList(nodes), layer: layer === '不限' ? null : layer, include_draft: draft },
        }),
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className={page.section} aria-labelledby={`${id}-preview`} style={{ marginTop: 16 }}>
      <div>
        <h2 id={`${id}-preview`} className={page.heading}>检索预览</h2>
        <p className={page.hint}>模拟一个决策点的局面,看会想起哪些记忆,以及写进提示词的样子。</p>
      </div>
      <div className={styles.previewGrid}>
        <Input label="局面里的名字" description="逗号分隔" value={entities} onChange={(e) => setEntities(e.target.value)} />
        <Input label="节点类型" description="如 reward、battle" value={nodes} onChange={(e) => setNodes(e.target.value)} />
        <Select label="决策层" options={['不限', '战略', '战术', '选择', '事件'].map((v) => ({ value: v, label: v }))} value={layer} onValueChange={setLayer} />
      </div>
      <div className={page.row} style={{ padding: 0, minHeight: 0 }}>
        <span className={page.rowTitle}>
          <Switch aria-label="包含待审核草稿" checked={draft} onCheckedChange={setDraft} />
          包含待审核草稿
        </span>
        <Button size="sm" variant="secondary" loading={loading} onClick={run}>
          预览检索结果
        </Button>
      </div>
      {result &&
        (result.items.length ? (
          <>
            <div className={page.group}>
              {result.items.map((r) => (
                <div key={r.id} className={page.row} style={{ minHeight: 52 }}>
                  <span className={page.rowTitle}>{r.title}</span>
                  <span className={page.rowHint}>
                    命中 {r.reasons.join('、')} · 得分 {r.score.toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
            <pre className={styles.prompt}>{result.prompt}</pre>
          </>
        ) : (
          <Alert tone="info" title="没有想起任何记忆">换一组名字或节点类型试试。</Alert>
        ))}
    </section>
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
  const [error, setError] = useState('');

  async function save() {
    setSaving(true);
    setError('');
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
      onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent title={memory ? '编辑记忆' : '添加记忆'} description="写清楚在什么条件下、该怎么做、为什么。修改公共记忆会保存为本机版本。">
        <div style={{ display: 'grid', gap: 16 }}>
          <SegmentedControl
            label="类别"
            options={['机制', '打法', '教训'].map((v) => ({ value: v, label: v }))}
            value={kind}
            onValueChange={(v) => setKind(v as Memory['kind'])}
            fullWidth
          />
          <Input label="标题" description="一句话说清这条经验" value={title} onChange={(e) => setTitle(e.target.value)} />
          <Textarea label="内容" rows={5} value={content} onChange={(e) => setContent(e.target.value)} />
          <Input label="触发名字" description="局面里出现这些名字时想起,逗号分隔" value={entities} onChange={(e) => setEntities(e.target.value)} />
          <div className={page.fields2}>
            <Input label="触发节点类型" description="如 reward" value={nodes} onChange={(e) => setNodes(e.target.value)} />
            <Input label="触发界面" description="如 prepare" value={screens} onChange={(e) => setScreens(e.target.value)} />
          </div>
          <div className={page.fields2}>
            <Input label="限定决策层" description="战略、战术、选择、事件,留空不限" value={scope} onChange={(e) => setScope(e.target.value)} />
            <Select
              label="置信度"
              options={Object.entries(CONFIDENCE).map(([value, label]) => ({ value, label }))}
              value={confidence}
              onValueChange={(v) => setConfidence(v as Memory['confidence'])}
            />
          </div>
          <Input label="来源" value={source} onChange={(e) => setSource(e.target.value)} />
          {error && <Alert tone="danger" title="没有保存">{error}</Alert>}
          <div className={page.actions}>
            <Button variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button loading={saving} disabled={!title.trim() || !content.trim()} onClick={save}>
              {memory ? '保存修改' : '添加记忆'}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
