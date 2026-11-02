import { BarChart3, Clock3, ShieldCheck, X } from "lucide-react";
import { EvidenceClipPlayer } from "./EvidenceClipPlayer";
import { attributionLabel, formatDistance, formatResponse } from "../lib/format";
import { useDialogFocus } from "../hooks/useDialogFocus";

function DetailMetric({ label, value }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

export function StaffGreetingDetailDrawer({ onClose, staff }) {
  const maxHourly = Math.max(...staff.hourly, 1);
  const dialogRef = useDialogFocus(onClose);

  return (
    <aside ref={dialogRef} aria-labelledby="staff-detail-title" aria-modal="true" className="detail-drawer" role="dialog">
      <header className="detail-drawer__header">
        <div>
          <span className="panel-kicker">员工详情 · DEMO DATA</span>
          <h2 id="staff-detail-title">{staff.staffName}</h2>
          <p>{staff.staffId} · <span className={"attribution-badge attribution-badge--" + staff.attribution}>{attributionLabel(staff.attribution)}</span></p>
        </div>
        <button aria-label="关闭员工详情" className="icon-button" onClick={onClose} type="button">
          <X aria-hidden="true" size={18} />
        </button>
      </header>

      <div className="detail-drawer__body">
        <div className="staff-detail-status">
          <span className={"state-dot state-dot--" + (staff.online ? "normal" : "unknown")} />
          {staff.online ? "当前在线" : "当前离线"}
          <small>Staff Role 与 Staff Identity 分开处理</small>
        </div>

        <dl className="detail-metric-grid">
          <DetailMetric label="今日迎宾" value={staff.greetingCount + " 次"} />
          <DetailMetric label="本周迎宾" value={staff.weekGreetingCount + " 次"} />
          <DetailMetric label="成功率" value={staff.successRate.toFixed(1) + "%"} />
          <DetailMetric label="平均响应" value={formatResponse(staff.averageResponseSeconds)} />
          <DetailMetric label="最快响应" value={formatResponse(staff.fastestResponseSeconds)} />
          <DetailMetric label="最慢响应" value={formatResponse(staff.slowestResponseSeconds)} />
          <DetailMetric label="平均最小距离" value={formatDistance(staff.averageDistanceMetres)} />
          <DetailMetric label="未观察到" value={staff.notObservedCount + " 次"} />
        </dl>

        <section className="drawer-section" aria-labelledby="hourly-title">
          <div className="drawer-section__heading">
            <BarChart3 aria-hidden="true" size={16} />
            <h3 id="hourly-title">按小时迎宾分布</h3>
          </div>
          <div className="hourly-bars">
            {staff.hourly.map((value, index) => (
              <div key={index}>
                <span className="hourly-bars__track"><i style={{ height: (value / maxHourly) * 100 + "%" }} /></span>
                <strong>{value}</strong>
                <small>{[12, 14, 16, 18][index]} 时</small>
              </div>
            ))}
          </div>
        </section>

        <section className="drawer-section" aria-labelledby="recent-staff-events-title">
          <div className="drawer-section__heading">
            <Clock3 aria-hidden="true" size={16} />
            <h3 id="recent-staff-events-title">最近迎宾事件</h3>
          </div>
          <ul className="recent-event-list">
            {staff.recentEvents.map((eventId, index) => (
              <li key={eventId}>
                <span className="recent-event-list__dot" />
                <div><strong>{eventId}</strong><small>{["18:18", "17:52", "17:31"][index]} · 已确认 · Evidence 已就绪</small></div>
              </li>
            ))}
          </ul>
        </section>

        <section className="drawer-section">
          <div className="drawer-section__heading">
            <ShieldCheck aria-hidden="true" size={16} />
            <h3>最近证据</h3>
          </div>
          <EvidenceClipPlayer evidence="已就绪" label={staff.staffName + " 最近迎宾"} />
        </section>
      </div>
    </aside>
  );
}
