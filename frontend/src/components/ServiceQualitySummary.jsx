import { CheckCircle2, CircleAlert, TimerReset } from "lucide-react";
import { DataAvailabilityNotice } from "./DataAvailabilityNotice";
import { DataSourceBadge } from "./DataSourceBadge";

const iconForTone = { positive: CheckCircle2, warning: CircleAlert, neutral: TimerReset };

export function ServiceQualitySummary({ demoGreetingSummary, demoSource = { kind: "unavailable" }, items = [], legacyGreetingSummary, onRetry, source = { kind: "demo" } }) {
  const isLegacyMock = source.kind === "mock_attribution";
  const hasItems = items.length > 0;
  const hasDemoSummary = demoSource.kind === "live" || Boolean(demoGreetingSummary?.rows?.length);
  return (
    <aside className="panel service-quality-panel" aria-labelledby="service-quality-title">
      <div className="panel-heading panel-heading--compact">
        <div>
          <div className="eyebrow"><span className="panel-kicker">服务质量摘要</span><DataSourceBadge source={source} /></div>
          <h2 id="service-quality-title">班次服务质量</h2>
        </div>
        <span className="quality-score">{hasItems ? "86.1" : "—"}{hasItems && <span>/100</span>}</span>
      </div>
      {hasItems ? <>
        <div className="quality-list">{items.map((item) => {
          const Icon = iconForTone[item.tone] || TimerReset;
          return <div className="quality-item" key={item.label}><span className={"quality-item__icon quality-item__icon--" + item.tone}><Icon aria-hidden="true" size={16} strokeWidth={1.9} /></span><div><span>{item.label}</span><strong>{item.value}</strong><small>{item.detail}</small></div><em className={"quality-status quality-status--" + item.tone}>{item.status}</em></div>;
        })}</div>
        <div className="quality-contract"><TimerReset aria-hidden="true" size={15} /><div><strong>响应时间口径</strong><span>进入 greeting_area → greeting confirmed</span></div></div>
      </> : (isLegacyMock || hasDemoSummary) ? <div className="legacy-greeting-summary">
        {isLegacyMock && <>
          <strong>历史确认迎宾（配置的演示归属）</strong>
          <p>共 {legacyGreetingSummary?.total || 0} 次。此接口没有 staff_id、日期范围、成功率或响应时间，不能作为员工绩效。</p>
          {legacyGreetingSummary?.rows?.length ? <ul>{legacyGreetingSummary.rows.map((row) => <li key={row.staffName}><span>{row.staffName}</span><strong>{row.greetingCount} 次</strong><small>最后记录 {row.lastGreetingAt}</small></li>)}</ul> : <p>暂无历史确认迎宾记录。</p>}
        </>}
        {hasDemoSummary && <div className="demo-greeting-summary">
          <strong>DEMO 迎宾排行</strong>
          <p>demo_relaxed 已确认 {demoGreetingSummary?.total || 0} 次，仅用于功能展示，不计入正式 KPI 或员工绩效。</p>
          {demoGreetingSummary?.rows?.length ? <ul>{demoGreetingSummary.rows.map((row) => <li key={row.staffName}><span>{row.staffName}</span><strong>{row.greetingCount} 次</strong><small>最后记录 {row.lastGreetingAt}</small></li>)}</ul> : <p>暂无 demo_relaxed 已确认迎宾。</p>}
        </div>}
      </div> : <DataAvailabilityNotice onRetry={onRetry} source={source} title="服务质量统计不可用" />}
    </aside>
  );
}
