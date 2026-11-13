import { Hourglass } from 'lucide-react';

import EmptyState from '@/components/arc/empty-state/empty-state';
import page from '@/lib/page.module.css';

export default function Placeholder({ title, description }: { title: string; description: string }) {
  return (
    <div className={page.page}>
      <header className={page.header}>
        <h1 className={page.title}>{title}</h1>
      </header>
      <EmptyState title={description} description=""  icon={<Hourglass size={20} strokeWidth={1.75} aria-hidden="true" />} />
    </div>
  );
}
