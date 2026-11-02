import { Clock3, Link2, ShieldCheck, UserRound, X } from "lucide-react";
import { EvidenceClipPlayer } from "./EvidenceClipPlayer";
import { attributionLabel, compactId, greetingLabel, greetingTone } from "../lib/format";
import { DataSourceBadge } from "./DataSourceBadge";
import { useDialogFocus } from "../hooks/useDialogFocus";

function Fact({ label, mono = false, value }) {
  return <div><dt>{label}</dt><dd className={mono ? "mono" : ""}>{value || "未提供"}</dd></div>;
}

function isGreetingTask(detail) {
  return detail.kind === "greeting-task" || Boolean(detail.status && !detail.type);
}

export function EventDetailDrawer({ detail, onClose, onOpenBinding, source = { kind: "demo" } }) {
  const greetingTask = isGreetingTask(detail);
  const title = greetingTask ? "迎宾任务详情" : "事件详情";
  const evidence = detail.evidence;
  const dialogRef = useDialogFocus(onClose);
  const facts = detail.facts || [
    { label: "发生时间", value: detail.time },
    { label: "事件记录", mono: true, value: detail.reference || detail.id },
    { label: "Track", mono: true, value: detail.trackLabel || detail.token || "未提供" },
    { label: "Visit ID", mono: true, value: detail.visitId ? compactId(detail.visitId) : "未提供" },
    { label: "摄像头", mono: true, value: detail.camera },
    { label: "置信度", value: detail.confidence },
    { label: "证据状态", value: typeof detail.evidence === "string" ? detail.evidence : detail.evidence?.status },
  ];

  return (
    <aside ref={dialogRef} aria-labelledby="event-detail-title" aria-modal="true" className="detail-drawer" role="dialog">
      <header className="detail-drawer__header">
        <div>
          <span className="panel-kicker">{greetingTask ? "活跃迎宾" : "AI 事件"} · <DataSourceBadge source={source} /></span>
          <h2 id="event-detail-title">{title}</h2>
          <p className="mono">{detail.id}</p>
        </div>
        <button aria-label="关闭详情" className="icon-button" onClick={onClose} type="button"><X aria-hidden="true" size={18} /></button>
      </header>
      <div className="detail-drawer__body">
        {greetingTask ? <>
          <div className="drawer-primary-row"><UserRound aria-hidden="true" size={18} /><div><strong>{detail.customer}</strong><span>{detail.member}</span></div><span className={"status-badge status-badge--" + greetingTone(detail.status)}>{greetingLabel(detail.status)}</span></div>
          <dl className="drawer-facts"><Fact label="员工" value={detail.staff} /><Fact label="员工 Track" mono value={detail.staffTrack} /><Fact label="归属状态" value={attributionLabel(detail.attribution)} /><Fact label="摄像头" mono value={detail.camera} /><Fact label="等待时长" value={detail.wait} /><Fact label="当前距离" value={detail.distance} /><Fact label="员工移动" value={detail.travel} /><Fact label="迎宾置信度" value={detail.confidence} /></dl>
          <section className="drawer-section" aria-labelledby="greeting-timeline-title"><div className="drawer-section__heading"><Clock3 aria-hidden="true" size={16} /><h3 id="greeting-timeline-title">服务时间线</h3></div><ol className="service-timeline"><li><span>18:23:54</span><strong>顾客进入 greeting_area</strong></li><li><span>{detail.eventTime}</span><strong>检测到员工接近</strong></li><li><span>当前</span><strong>{greetingLabel(detail.status)}</strong></li></ol></section>
          {detail.attribution === "unknown" && source.kind === "demo" && <button className="binding-action" onClick={() => onOpenBinding(detail)} type="button"><Link2 aria-hidden="true" size={16} />人工绑定当前 Staff Track</button>}
        </> : <>
          <div className="drawer-primary-row"><ShieldCheck aria-hidden="true" size={18} /><div><strong>{detail.type}</strong><span>{detail.subject}</span></div><span className="status-badge status-badge--info">{detail.state}</span></div>
          <dl className="drawer-facts">{facts.map((fact) => <Fact key={fact.label} label={fact.label} mono={fact.mono} value={fact.value} />)}</dl>
        </>}
        <section className="drawer-section"><div className="drawer-section__heading"><ShieldCheck aria-hidden="true" size={16} /><h3>Evidence</h3></div><EvidenceClipPlayer evidence={evidence} label={detail.id} /></section>
      </div>
    </aside>
  );
}
