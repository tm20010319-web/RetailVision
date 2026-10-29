import {
  Activity,
  Camera,
  ChevronDown,
  CircleAlert,
  Cloud,
  Moon,
  Radio,
  Sun,
} from "lucide-react";
import { statusLabel } from "../lib/format";
import "./phase1-gap-fixes.css";

const serviceIcons = {
  api: Cloud,
  push: Radio,
  poll: Activity,
  behavior: Activity,
  face: Camera,
  vest: Camera,
  evidence: CircleAlert,
};

function HealthChip({ service, open, onToggle }) {
  const Icon = serviceIcons[service.id] || Activity;

  return (
    <div className="health-chip-wrap">
      <button
        aria-expanded={open}
        aria-haspopup="dialog"
        className="health-chip"
        onClick={() => onToggle(service.id)}
        type="button"
      >
        <span className={"state-dot state-dot--" + service.state} />
        <span className="health-chip__label">{service.label}</span>
        <span className="health-chip__state">{statusLabel(service.state)}</span>
        <span className="health-chip__value">{service.compact}</span>
        <ChevronDown aria-hidden="true" size={12} strokeWidth={1.8} />
      </button>
      {open && (
        <div className="health-popover" role="dialog" aria-label={service.label + " 状态详情"}>
          <div className="health-popover__title">
            <Icon aria-hidden="true" size={15} strokeWidth={1.8} />
            <strong>{service.label}</strong>
            <span className={"status-badge status-badge--" + service.state}>
              {statusLabel(service.state)}
            </span>
          </div>
          <dl className="health-popover__facts">
            <div>
              <dt>最后心跳</dt>
              <dd>{service.heartbeat}</dd>
            </div>
            <div>
              <dt>延迟</dt>
              <dd>{service.latency}</dd>
            </div>
            <div>
              <dt>版本</dt>
              <dd>{service.version}</dd>
            </div>
            <div>
              <dt>摄像头</dt>
              <dd>{service.camera}</dd>
            </div>
            <div>
              <dt>最近错误</dt>
              <dd>{service.error}</dd>
            </div>
          </dl>
        </div>
      )}
    </div>
  );
}

export function StoreHealthBar({
  healthServices,
  openHealthId,
  onHealthToggle,
  onThemeToggle,
  store,
  theme,
}) {
  const ThemeIcon = theme === "dark" ? Sun : Moon;

  return (
    <>
      <a className="skip-link" href="#main-content">
        跳至主内容
      </a>
      <header className="store-health-bar">
        <div className="store-health-bar__identity">
          <span className="store-mark" aria-hidden="true">RV</span>
          <div>
            <h1>AI 门店运营台</h1>
            <p>{store.name} <span>{store.code}</span></p>
          </div>
        </div>

        <div className="store-health-bar__context">
          <time dateTime="2026-07-30T18:24:36">{store.time}</time>
          <span>{store.shift}</span>
        </div>

        <div className="camera-online">
          <Camera aria-hidden="true" size={15} strokeWidth={1.8} />
          <span>摄像头在线</span>
          <strong>{store.cameraOnline}</strong>
        </div>

        <div className="health-chip-list" aria-label="系统健康状态">
          {healthServices.map((service) => (
            <HealthChip
              key={service.id}
              onToggle={onHealthToggle}
              open={openHealthId === service.id}
              service={service}
            />
          ))}
        </div>

        <button
          aria-label={theme === "dark" ? "切换至浅色模式" : "切换至深色模式"}
          className="icon-button"
          onClick={onThemeToggle}
          title={theme === "dark" ? "切换至浅色模式" : "切换至深色模式"}
          type="button"
        >
          <ThemeIcon aria-hidden="true" size={17} strokeWidth={1.8} />
        </button>
      </header>
    </>
  );
}
