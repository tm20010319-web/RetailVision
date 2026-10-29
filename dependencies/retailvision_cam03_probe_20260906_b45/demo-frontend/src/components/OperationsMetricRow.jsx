import { Info } from "lucide-react";

export function OperationsMetricRow({ metrics }) {
  return (
    <section className="metric-band" aria-labelledby="metric-row-title">
      <div className="metric-band__title">
        <div>
          <span className="panel-kicker">今日指标</span>
          <h2 id="metric-row-title">门店服务概览</h2>
        </div>
        <span className="demo-label">DEMO DATA</span>
      </div>
      <div className="metric-grid">
        {metrics.map((metric) => (
          <article className="metric-item" key={metric.label}>
            <div className="metric-item__label">
              <span>{metric.label}</span>
              {metric.tooltip && (
                <button aria-label={metric.tooltip} className="tooltip-button" title={metric.tooltip} type="button">
                  <Info aria-hidden="true" size={14} strokeWidth={1.8} />
                </button>
              )}
            </div>
            <strong>{metric.value}</strong>
            <div className="metric-item__footer">
              <span>{metric.detail}</span>
              <em className={"metric-trend metric-trend--" + metric.tone}>{metric.trend}</em>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
