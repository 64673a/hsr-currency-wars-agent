import { Alert, Button, Drawer, EmptyState, List, ListItem, Pagination, Progress, Search, Stat, Tab, Tabs, Tag } from '@arclux/arc-ui-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { api, type Item, type KnowledgeMeta, type SyncState } from '../api';
import { errorText, toast } from '../ui';
import { Detail, summaryLine } from './details';
import MemoryPanel from './MemoryPanel';

const KINDS: { kind: string; name: string }[] = [
  { kind: 'roles', name: '角色' },
  { kind: 'traits', name: '羁绊' },
  { kind: 'equipment', name: '装备' },
  { kind: 'augments', name: '投资策略' },
  { kind: 'portals', name: '投资环境' },
  { kind: 'spotlights', name: '主角光环' },
  { kind: 'companions', name: '奇遇' },
  { kind: 'guides', name: '指引卡' },
  { kind: 'memories', name: '记忆' },
];
const PAGE_SIZE = 40;

export default function KnowledgePage() {
  const [meta, setMeta] = useState<KnowledgeMeta>({});
  const [tab, setTab] = useState(0);
  const [sync, setSync] = useState<SyncState | null>(null);
  const timer = useRef<number | null>(null);

  const loadMeta = useCallback(async () => {
    try {
      setMeta((await api<{ meta: KnowledgeMeta }>('/api/knowledge/meta')).meta);
    } catch (e) {
      toast(errorText(e), 'error');
    }
  }, []);

  const poll = useCallback(async () => {
    const s = await api<SyncState>('/api/knowledge/sync');
    setSync(s);
    if (s.running) {
      timer.current = window.setTimeout(poll, 1000);
    } else if (s.error) {
      toast(`同步失败:${s.error}`, 'error');
    } else if (s.result) {
      toast('同步完成', 'success');
      loadMeta();
    }
  }, [loadMeta]);

  useEffect(() => {
    loadMeta();
    api<SyncState>('/api/knowledge/sync').then((s) => {
      setSync(s);
      if (s.running) poll();
    });
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [loadMeta, poll]);

  async function startSync() {
    try {
      setSync(await api<SyncState>('/api/knowledge/sync', { method: 'POST', body: {} }));
      timer.current = window.setTimeout(poll, 800);
    } catch (e) {
      toast(errorText(e), 'error');
    }
  }

  const counts = meta.counts ?? {};
  const running = sync?.running ?? false;
  return (
    <div className="page">
      <div className="page-title">
        <h1>知识库</h1>
        <Button size="sm" loading={running} onClick={startSync}>
          {meta.synced_at ? '重新同步' : '同步官方数据'}
        </Button>
      </div>
      {meta.synced_at ? (
        <div className="grid2" style={{ marginBottom: 12 }}>
          <Stat size="sm" value={`S${meta.season_id}-${meta.sub_season_id}`} label="赛季" />
          <Stat size="sm" value={meta.game_version ?? ''} label="游戏版本" />
          <Stat size="sm" value={meta.synced_at.slice(0, 16).replace('T', ' ')} label="同步时间" />
          <Stat size="sm" value={String(counts.guides ?? 0)} label="指引卡" />
        </div>
      ) : (
        !running && <Alert variant="info">知识库还没有同步。点击右上角的按钮从米游社官方接口拉取数据(约 1 分钟)。</Alert>
      )}
      {running && (
        <div style={{ margin: '8px 0 16px' }}>
          <Progress indeterminate label={sync?.message || '同步中'} />
        </div>
      )}
      {meta.warnings && meta.warnings.length > 0 && (
        <Alert variant="warning" heading="提示">
          {meta.warnings.join(';')}
        </Alert>
      )}
      <Tabs selected={tab} onArcChange={(e) => setTab(e.detail.value)}>
        {KINDS.map((k, i) => (
          <Tab key={k.kind} label={k.kind === 'memories' ? k.name : `${k.name} ${counts[k.kind] ?? ''}`}>
            {i === tab && (k.kind === 'memories' ? <MemoryPanel /> : <KindList kind={k.kind} name={k.name} synced={!!meta.synced_at} />)}
          </Tab>
        ))}
      </Tabs>
    </div>
  );
}

function KindList({ kind, name, synced }: { kind: string; name: string; synced: boolean }) {
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ total: number; items: Item[] }>({ total: 0, items: [] });
  const [open, setOpen] = useState<Item | null>(null);

  useEffect(() => {
    let cancelled = false;
    const t = window.setTimeout(async () => {
      try {
        const params = new URLSearchParams({ q, limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
        const d = await api<{ total: number; items: Item[] }>(`/api/knowledge/${kind}?${params}`);
        if (!cancelled) setData(d);
      } catch (e) {
        toast(errorText(e), 'error');
      }
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [kind, q, page, synced]);

  async function show(it: Item) {
    try {
      setOpen(await api<Item>(`/api/knowledge/${kind}/${encodeURIComponent(it.id)}`));
    } catch (e) {
      toast(errorText(e), 'error');
    }
  }

  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  return (
    <div style={{ marginTop: 12 }}>
      <div className="row" style={{ marginBottom: 10 }}>
        <div className="grow">
          <Search
            placeholder={`搜索${name}(名称、描述、羁绊…)`}
            value={q}
            onArcInput={(e) => {
              setQ(e.detail.value);
              setPage(1);
            }}
            onArcClear={() => setQ('')}
          />
        </div>
        <span className="muted">共 {data.total} 条</span>
      </div>
      {data.items.length === 0 ? (
        <EmptyState heading={synced ? '没有匹配的条目' : '知识库还没有同步'} />
      ) : (
        <List variant="separated">
          {data.items.map((it) => (
            <ListItem key={it.id} onClick={() => show(it)}>
              {it.icon ? <img slot="prefix" className="icon" src={it.icon} alt="" loading="lazy" /> : null}
              <span>
                {it.name}{' '}
                {it.flow && (
                  <Tag size="sm" variant="warning">
                    改变流程
                  </Tag>
                )}
                {it.support_hard && (
                  <Tag size="sm" variant="primary">
                    高难
                  </Tag>
                )}
                {it.unused && <Tag size="sm">本赛季未使用</Tag>}
              </span>
              <span slot="description">{summaryLine(kind, it)}</span>
            </ListItem>
          ))}
        </List>
      )}
      {pages > 1 && (
        <div className="row" style={{ marginTop: 12, justifyContent: 'center' }}>
          <Pagination total={pages} current={page} onArcChange={(e) => setPage(e.detail.value)} />
        </div>
      )}
      <Drawer open={!!open} position="right" heading={name} onArcClose={() => setOpen(null)}>
        {open && (
          <div style={{ padding: '4px 4px 24px', maxWidth: 560 }}>
            <Detail kind={kind} d={open} />
          </div>
        )}
      </Drawer>
    </div>
  );
}
