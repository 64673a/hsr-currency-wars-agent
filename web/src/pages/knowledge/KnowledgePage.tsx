import { useCallback, useEffect, useRef, useState } from 'react';

import { Alert } from '@/components/arc/alert/alert';
import { Button } from '@/components/arc/button/button';
import { Drawer, DrawerContent } from '@/components/arc/drawer/drawer';
import EmptyState from '@/components/arc/empty-state/empty-state';
import { MetricCard } from '@/components/arc/metric-card/metric-card';
import { Pagination } from '@/components/arc/pagination/pagination';
import { Progress } from '@/components/arc/progress/progress';
import { SearchField } from '@/components/arc/search-field/search-field';
import { Skeleton } from '@/components/arc/skeleton/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/arc/tabs/tabs';
import { api, enc, errorText, type Item, type KnowledgeMeta, type SyncState } from '@/lib/api';
import page from '@/lib/page.module.css';
import { useToast } from '@/lib/toast';

import { Detail, RowBadges, summaryLine } from './details';
import styles from './knowledge.module.css';
import MemoryPanel from './MemoryPanel';

const KINDS = [
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
const PAGE_SIZE = 30;

// 同步进度:按阶段和日志里的数字估算百分比
function syncPercent(s: SyncState | null): number {
  if (!s) return 0;
  const num = (re: RegExp) => s.message.match(re);
  switch (s.stage) {
    case 'config':
      return 5;
    case 'lineups': {
      const m = num(/(\d+)/);
      return 10 + Math.min(60, (Number(m?.[1] ?? 0) / 300) * 60);
    }
    case 'build':
      return 72;
    case 'write':
      return 78;
    case 'templates': {
      const m = num(/(\d+)\/(\d+)/);
      return m ? 80 + (Number(m[1]) / Number(m[2])) * 20 : 80;
    }
    default:
      return s.running ? 2 : 100;
  }
}

export default function KnowledgePage() {
  const toast = useToast();
  const [meta, setMeta] = useState<KnowledgeMeta | null>(null);
  const [tab, setTab] = useState('roles');
  const [sync, setSync] = useState<SyncState | null>(null);
  const timer = useRef<number | null>(null);

  const loadMeta = useCallback(async () => {
    try {
      setMeta((await api<{ meta: KnowledgeMeta }>('/api/knowledge/meta')).meta);
    } catch {
      setMeta({});
    }
  }, []);

  const poll = useCallback(async () => {
    const s = await api<SyncState>('/api/knowledge/sync');
    setSync(s);
    if (s.running) timer.current = window.setTimeout(poll, 800);
    else if (s.result) {
      toast('知识库同步完成', '已更新为官方最新数据');
      loadMeta();
    }
  }, [loadMeta, toast]);

  useEffect(() => {
    loadMeta();
    api<SyncState>('/api/knowledge/sync')
      .then((s) => {
        setSync(s);
        if (s.running) poll();
      })
      .catch(() => {});
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [loadMeta, poll]);

  async function startSync() {
    try {
      setSync(await api<SyncState>('/api/knowledge/sync', { method: 'POST', body: {} }));
      timer.current = window.setTimeout(poll, 600);
    } catch (e) {
      setSync({ running: false, stage: '', message: '', result: null, error: errorText(e) });
    }
  }

  const counts = meta?.counts ?? {};
  const running = sync?.running ?? false;
  const synced = !!meta?.synced_at;
  return (
    <div className={page.page}>
      <header className={page.header}>
        <div>
          <h1 className={page.title}>知识库</h1>
          <p className={page.subtitle}>
            {synced
              ? `赛季 S${meta?.season_id}-${meta?.sub_season_id} · 游戏版本 ${meta?.game_version} · 同步于 ${meta?.synced_at?.slice(0, 16).replace('T', ' ')}`
              : '来自米游社货币战争专题页的官方数据,以及积累的记忆'}
          </p>
        </div>
        <Button loading={running} onClick={startSync}>
          {synced ? '重新同步' : '同步官方数据'}
        </Button>
      </header>

      {running && (
        <div className={styles.syncBox} aria-live="polite">
          <Progress value={syncPercent(sync)} label={sync?.message || '正在同步'} showValue />
        </div>
      )}
      {sync?.error && !running && (
        <Alert tone="danger" title="同步失败">
          {sync.error}
        </Alert>
      )}

      {meta === null ? (
        <Skeleton lines={2} />
      ) : synced ? (
        <div className={page.metrics}>
          <MetricCard label="角色" value={counts.roles ?? 0} suffix=" 名" context="1 到 5 费,外加 7 费的阿哈" />
          <MetricCard label="投资策略" value={counts.augments ?? 0} suffix=" 条" context="银色、金色、棱彩三档" />
          <MetricCard label="装备" value={counts.equipment ?? 0} suffix=" 件" context="含合成原料与特权装备" />
          <MetricCard label="指引卡" value={counts.guides ?? 0} suffix=" 张" context="按使用量排序的官方阵容" />
        </div>
      ) : (
        <EmptyState
          title="知识库还是空的"
          description="同步一次官方数据(约 1 分钟),就能浏览角色、羁绊、装备、投资策略和热门阵容。"
          action={
            <Button size="sm" loading={running} onClick={startSync}>
              同步官方数据
            </Button>
          }
        />
      )}

      {meta?.warnings && meta.warnings.length > 0 && (
        <Alert tone="warning" title="有些数据需要从游戏内补充">
          {meta.warnings.join(';')}
        </Alert>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList aria-label="知识库分类">
          {KINDS.map((k) => (
            <TabsTrigger key={k.kind} value={k.kind}>
              {k.name}
            </TabsTrigger>
          ))}
        </TabsList>
        {KINDS.map((k) => (
          <TabsContent key={k.kind} value={k.kind}>
            {tab === k.kind &&
              (k.kind === 'memories' ? <MemoryPanel /> : <KindList kind={k.kind} name={k.name} total={counts[k.kind]} synced={synced} />)}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}

function KindList({ kind, name, total, synced }: { kind: string; name: string; total?: number; synced: boolean }) {
  const [q, setQ] = useState('');
  const [pageNo, setPageNo] = useState(1);
  const [data, setData] = useState<{ total: number; items: Item[] } | null>(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<Item | null>(null);

  useEffect(() => {
    let cancelled = false;
    const t = window.setTimeout(async () => {
      try {
        const params = new URLSearchParams({ q, limit: String(PAGE_SIZE), offset: String((pageNo - 1) * PAGE_SIZE) });
        const d = await api<{ total: number; items: Item[] }>(`/api/knowledge/${kind}?${params}`);
        if (!cancelled) {
          setData(d);
          setError('');
        }
      } catch (e) {
        if (!cancelled) setError(errorText(e));
      }
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [kind, q, pageNo, synced]);

  async function show(it: Item) {
    try {
      setOpen(await api<Item>(`/api/knowledge/${kind}/${enc(it.id)}`));
    } catch (e) {
      setError(errorText(e));
    }
  }

  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));
  return (
    <div className={page.section} style={{ marginTop: 16 }}>
      <div className={styles.toolbar}>
        <SearchField
          label={`搜索${name}`}
          placeholder="名称、描述、羁绊…"
          value={q}
          onValueChange={(v) => {
            setQ(v);
            setPageNo(1);
          }}
        />
        <span className={styles.count}>{data ? (q ? `找到 ${data.total} 条` : `共 ${total ?? data.total} 条`) : ''}</span>
      </div>
      {error && <Alert tone="danger" title="读取失败">{error}</Alert>}
      {data === null ? (
        <Skeleton lines={6} avatar />
      ) : data.items.length === 0 ? (
        <EmptyState
          title={synced ? (q ? '没有匹配的条目' : `还没有${name}`) : '知识库还是空的'}
          description={synced ? (q ? '换个关键词试试,也可以搜索羁绊名或描述里的字。' : kind === 'companions' ? '奇遇伙伴在对局中遇到时自动记录。' : '') || '没有数据。' : '先同步一次官方数据。'}
        />
      ) : (
        <div className={page.group}>
          {data.items.map((it) => (
            <button key={it.id} type="button" className={`${page.row} ${page.rowButton}`} onClick={() => show(it)}>
              <span className={page.rowMain}>
                {it.icon && <img className={page.icon} src={it.icon} alt="" loading="lazy" />}
                <span className={page.rowText}>
                  <span className={page.rowTitle}>
                    {it.name}
                    <RowBadges kind={kind} it={it} />
                  </span>
                  <span className={page.rowHint}>{summaryLine(kind, it)}</span>
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
      {pageCount > 1 && (
        <div className={styles.pager}>
          <Pagination page={pageNo} pageCount={pageCount} onPageChange={setPageNo} label={`${name}分页`} />
        </div>
      )}
      <Drawer open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        {open && (
          <DrawerContent title={open.name ?? open.title} description={name} side="right">
            <Detail kind={kind} d={open} />
          </DrawerContent>
        )}
      </Drawer>
    </div>
  );
}
