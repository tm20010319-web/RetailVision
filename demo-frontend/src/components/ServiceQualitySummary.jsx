import { CheckCircle2, CircleAlert, TimerReset } from "lucide-react";

const iconForTone = {
  positive: CheckCircle2,
  warning: CircleAlert,
  neutral: TimerReset,
};

export function ServiceQualitySummary({ items }) {
  return (
    <aside className="panel service-quality-panel" aria-labelledby="service-quality-title">
      <div className="panel-heading panel-heading--compact">
        <div>
          <div className="eyebrow">
            <span className="panel-kicker">服务质量摘要</span>
            <span className="demo-label">DEMO DATA</span>
          </div>
          <h2 id="service-quality-title">班次服务质量</h2>
        </div>
        <span className="quality-score">86.1<span>/100</span></span>
      </div>
      <div className="quality-list">
        {items.map((item) => {
          const Icon = iconForTone[item.tone] || TimerReset;
          return (
            <div className="quality-item" key={item.label}>
              <span className={"quality-item__icon quality-item__icon--" + item.tone}>
                <Icon aria-hidden="true" size={16} strokeWidth={1.9} />
              </span>
              <div>
                <span>{item.label}</span>
                <strong>{item.value}</strong>
                <small>{item.detail}</small>
              </div>
              <em className={"quality-status quality-status--" + item.tone}>{item.status}</em>
            </div>
          );
        })}
      </div>
      <div className="quality-contract">
        <TimerReset aria-hidden="true" size={15} />
        <div>
          <strong>响应时间口径</strong>
          <span>进入 greeting_area → greeting confirmed</span>
        </div>
      </div>
    </aside>
  );
}
