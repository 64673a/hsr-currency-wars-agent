import { EmptyState } from '@arclux/arc-ui-react';

export default function Placeholder({ title, text }: { title: string; text: string }) {
  return (
    <div className="page">
      <div className="page-title">
        <h1>{title}</h1>
      </div>
      <div className="placeholder">
        <EmptyState heading="还没有实现" description={text} />
      </div>
    </div>
  );
}
