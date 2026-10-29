import { Clock3, Link2, ShieldCheck, UserRound, X } from "lucide-react";
import { EvidenceClipPlayer } from "./EvidenceClipPlayer";
import { attributionLabel, compactId, greetingLabel, greetingTone } from "../lib/format";

function Fact({ label, value, mono = false }) {
  return <div><dt>{label}</dt><dd className={mono ? "mono" : ""}>{value}</dd></div>;
}

export function EventDetailDrawer({ detail, onClose, onOpenBinding }) {
  const isGreeting = Boolean(detail.status);
  const title = isGreeting ? "迎宾任务详情" : "事件详情";
  const detailId = isGreeting ? detail.id : detail.id;
  const evidence = detail.evidence;

  return (
    <aside aria-labelledby="event-detail-title" aria-modal="true" className="detail-drawer" role="dialog">
      <header className="detail-drawer__header">
        <div>
          <span className="panel-kicker">{isGreeting ? "活跃迎宾 · DEMO DATA" : "AI 事件 · DEMO DATA"}</span>
          <h2 id="event-detail-title">{title}</h2>
          <p className="mono">{detailId}</p>
        </div>
        <button aria-label="关闭详情" className="icon-button" onClick={onClose} type="button">
          <X aria-hidden="true" size={18} />
        </button>
      </header>

      <div className="detail-drawer__body">
        {isGreeting ? (
          <>
            <div className="drawer-primary-row">
              <UserRound aria-hidden="true" size={18} />
              <div>
                <strong>{detail.customer}</strong>
                <span>{detail.member}</span>
              </div>
              <span className={"status-badge status-badge--" + greetingTone(detail.status)}>{greetingLabel(detail.status)}</span>
            </div>
            <dl className="drawer-facts">
              <Fact label="员工" value={detail.staff} />
              <Fact label="员工 Track" mono value={detail.staffTrack} />
              <Fact label="归属状态" value={attributionLabel(detail.attribution)} />
              <Fact label="摄像头" mono value={detail.camera} />
              <Fact label="等待时长" value={detail.wait} />
              <Fact label="当前距离" value={detail.distance} />
              <Fact label="员工移动" value={detail.travel} />
              <Fact label="迎宾置信度" value={detail.confidence} />
            </dl>
            <section className="drawer-section" aria-labelledby="greeting-timeline-title">
              <div className="drawer-section__heading">
                <Clock3 aria-hidden="true" size={16} />
                <h3 id="greeting-timeline-title">服务时间线</h3>
              </div>
              <ol className="service-timeline">
                <li><span>18:23:54</span><strong>顾客进入 greeting_area</strong></li>
                <li><span>{detail.eventTime}</span><strong>检测到员工接近</strong></li>
                <li><span>当前</span><strong>{greetingLabel(detail.status)}</strong></li>
              </ol>
            </section>
            {detail.attribution === "unknown" && (
              <button className="binding-action" onClick={() => onOpenBinding(detail)} type="button">
                <Link2 aria-hidden="true" size={16} />
                人工绑定当前 Staff Track
              </button>
            )}
          </>
        ) : (
          <>
            <div className="drawer-primary-row">
              <ShieldCheck aria-hidden="true" size={18} />
              <div>
                <strong>{detail.type}</strong>
                <span>{detail.subject}</span>
              </div>
              <span className="status-badge status-badge--info">{detail.state}</span>
            </div>
            <dl className="drawer-facts">
              <Fact label="发生时间" value={detail.time} />
              <Fact label="Track Token" mono value={detail.token} />
              <Fact label="Visit ID" mono value={compactId(detail.visitId)} />
              <Fact label="摄像头" mono value={detail.camera} />
              <Fact label="置信度" value={detail.confidence} />
              <Fact label="证据状态" value={detail.evidence} />
            </dl>
          </>
        )}
        <section className="drawer-section">
          <div className="drawer-section__heading">
            <ShieldCheck aria-hidden="true" size={16} />
            <h3>Evidence</h3>
          </div>
          <EvidenceClipPlayer evidence={evidence} label={detailId} />
        </section>
      </div>
    </aside>
  );
}
