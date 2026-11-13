import { AppShell, Sidebar, SidebarLink, SidebarSection, Toast, TopBar } from '@arclux/arc-ui-react';
import { useEffect, useState } from 'react';

import KnowledgePage from './pages/KnowledgePage';
import Placeholder from './pages/Placeholder';
import SettingsPage from './pages/SettingsPage';

const PAGES: { path: string; label: string; section: string }[] = [
  { path: '#/watch', label: '观战', section: '对局' },
  { path: '#/runs', label: '对局记录', section: '对局' },
  { path: '#/compare', label: '模型对比', section: '对局' },
  { path: '#/knowledge', label: '知识库', section: '数据' },
  { path: '#/settings', label: '设置', section: '数据' },
];

function currentPath(): string {
  const hash = window.location.hash.split('?')[0];
  return PAGES.some((p) => p.path === hash) ? hash : '#/knowledge';
}

export default function App() {
  const [path, setPath] = useState(currentPath());

  useEffect(() => {
    const onHash = () => setPath(currentPath());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const sections = [...new Set(PAGES.map((p) => p.section))];
  let page;
  if (path === '#/settings') page = <SettingsPage />;
  else if (path === '#/knowledge') page = <KnowledgePage />;
  else if (path === '#/watch') page = <Placeholder title="观战" text="实时观看 agent 对局:截图、局面、模型思考和动作。随全自动对局一起实现。" />;
  else if (path === '#/runs') page = <Placeholder title="对局记录" text="历史对局列表和逐步回放。随决策层的对局记录一起实现。" />;
  else page = <Placeholder title="模型对比" text="同一批局面下并排比较多个模型的思考与决策。随离线回放一起实现。" />;

  return (
    <>
      <AppShell>
        <TopBar slot="topbar" heading="货币战争 Agent" />
        <Sidebar slot="sidebar" active={path}>
          {sections.map((s) => (
            <SidebarSection key={s} heading={s}>
              {PAGES.filter((p) => p.section === s).map((p) => (
                <SidebarLink key={p.path} href={p.path} active={p.path === path}>
                  {p.label}
                </SidebarLink>
              ))}
            </SidebarSection>
          ))}
        </Sidebar>
        {page}
      </AppShell>
      <Toast position="top-right" />
    </>
  );
}
