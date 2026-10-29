import { CircleDot, Filter, PlaySquare } from "lucide-react";
import { compactId } from "../../lib/format";
import { DataAvailabilityNotice } from "../DataAvailabilityNotice";
import { DataSourceBadge } from "../DataSourceBadge";

const filters = [
  { id: "all", label: "全部" },
  { id: "entry", label: "进店" },
  { id: "member", label: "会员" },
  { id: "greeting", label: "迎宾" },
  { id: "product", label: "商品行为" },
  { id: "exception", label: "异常" },
  { id: "shadow", label: "V2 Shadow" },
];

function evidenceLabel(evidence) {
  return typeof evidence === "string" ? evidence : (evidence?.status || "无证据");
}

export function RealtimeEventTimeline({ activeFilter, events = [], onFilterChange, onRetry, onSelect, source = { kind: "demo" } }) {
  const unavailable = source.kind !== "demo" && source.kind !== "live" && !events.length;
  return (
    <section className="panel event-timeline-panel" aria-labelledby="event-timeline-title">
      <div className="panel-heading panel-heading--timeline">
        <div>
          <div className="eyebrow"><span className="panel-kicker">AI 事件流</span><DataSourceBadge source={source} /></div>
          <h2 id="event-timeline-title">实时 AI 事件</h2>
        </div>
        <div className="event-filters" role="group" aria-label="事件筛选">
          <Filter aria-hidden="true" size={14} strokeWidth={1.8} />
          {filters.map((filter) => <button aria-pressed={activeFilter === filter.id} className={activeFilter === filter.id ? "is-active" : ""} key={filter.id} onClick={() => onFilterChange(filter.id)} type="button">{filter.label}</button>)}
        </div>
      </div>
      {unavailable ? <DataAvailabilityNotice onRetry={onRetry} source={source} title="近期事件不可用" /> : (
        <div className="event-timeline" aria-live="polite">
          {events.length ? events.map((event) => (
            <button className="event-row" key={event.id} onClick={() => onSelect(event)} type="button">
              <span className={"event-row__rail event-row__rail--" + event.category}><CircleDot aria-hidden="true" size={15} strokeWidth={1.9} /></span>
              <time className="mono">{event.time}</time>
              <span className="event-row__type">{event.type}{event.demo ? <span className="event-demo-badge">DEMO</span> : null}</span>
              <span className="event-row__subject">{event.subject}</span>
              <span className="event-row__meta">
                <span className="event-row__ids">
                  {event.trackLabel ? <span className="mono" title={event.trackLabel}>{event.trackLabel}</span> : null}
                  {event.visitId ? <span className="mono" title={event.visitId}>V {compactId(event.visitId)}</span> : null}
                  {event.token ? <span className="mono" title={event.token}>T {compactId(event.token)}</span> : null}
                  {event.reference ? <span className="mono" title={event.id}>{event.reference}</span> : null}
                </span>
                <small>{event.camera}</small>
              </span>
              <span className="event-row__state">{event.state} · {event.confidence}</span>
              <span className="event-row__evidence"><PlaySquare aria-hidden="true" size={14} />{evidenceLabel(event.evidence)}</span>
            </button>
          )) : <div className="empty-inline">当前筛选条件下没有事件。</div>}
        </div>
      )}
    </section>
  );
}
