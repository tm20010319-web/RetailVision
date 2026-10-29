import { AlertCircle, CircleStop, Play, RotateCcw, ShieldCheck, Video } from "lucide-react";

const stateLabels = {
  error: "异常",
  idle: "待启动",
  running: "实时检测中",
  starting: "正在启动",
  stopped: "已停止",
  stopping: "正在停止",
};

function stateTone(status) {
  if (status === "running") return "normal";
  if (["starting", "stopping", "idle", "stopped"].includes(status)) return "warning";
  return "error";
}

function displayTime(value) {
  if (!value) return "暂无";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value).replace("T", " ").slice(11, 19);
  return new Intl.DateTimeFormat("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

export function DemoGreetingControl({ control }) {
  const status = control.status;
  const workerState = status?.status || (control.error ? "error" : "idle");
  const running = Boolean(status?.running);
  const busy = Boolean(control.pendingAction);
  const lastEvent = status?.last_event;

  return (
    <section className="demo-greeting-control" aria-labelledby="demo-greeting-title">
      <div className="demo-greeting-control__identity">
        <span className="demo-greeting-control__icon" aria-hidden="true"><ShieldCheck size={17} strokeWidth={1.8} /></span>
        <div>
          <div className="eyebrow"><span className="event-demo-badge">DEMO ONLY</span><span>RTSP 现场动作触发</span></div>
          <h2 id="demo-greeting-title">迎宾演示控制</h2>
          <p>蓝马甲员工与另一人持续同框并产生移动后，才会写入 Demo 迎宾事件。</p>
        </div>
      </div>

      <dl className="demo-greeting-control__facts">
        <div><dt>Worker</dt><dd><span className={"status-badge status-badge--" + stateTone(workerState)}>{stateLabels[workerState] || workerState}</span></dd></div>
        <div><dt>当前会话</dt><dd className="tabular">{status?.session_event_count ?? 0} 次触发</dd></div>
        <div><dt>当前画面</dt><dd className="tabular">{status ? (status.person_count + " 人 / " + status.staff_count + " 蓝马甲") : "等待服务"}</dd></div>
        <div><dt>最新事件</dt><dd>{lastEvent ? displayTime(lastEvent.occurred_at) : "暂无"}</dd></div>
      </dl>

      <div className="demo-greeting-control__actions" aria-label="Demo 迎宾控制">
        <button className="demo-control-button demo-control-button--primary" disabled={busy} onClick={control.start} type="button">
          <Play aria-hidden="true" size={14} strokeWidth={2} />{running ? "新一轮 Demo" : "开始 Demo"}
        </button>
        <button className="demo-control-button" disabled={busy || !running} onClick={control.reset} title="清理当前 Track 对并开始新会话" type="button">
          <RotateCcw aria-hidden="true" size={14} strokeWidth={1.9} />重置
        </button>
        <button className="demo-control-button demo-control-button--stop" disabled={busy || !running} onClick={control.stop} title="停止独立 RTSP Demo Worker" type="button">
          <CircleStop aria-hidden="true" size={14} strokeWidth={1.9} />停止
        </button>
      </div>

      <div className="demo-greeting-control__footer">
        <span><Video aria-hidden="true" size={13} strokeWidth={1.8} />{status?.source || "Demo 服务未连接"}</span>
        {control.error && <span className="demo-greeting-control__error"><AlertCircle aria-hidden="true" size={13} strokeWidth={1.8} />{control.error.message || "Demo 服务不可用"}</span>}
        {!control.error && lastEvent && <span className="demo-greeting-control__event">事件已写入行为流，员工身份未绑定，证据待生成。</span>}
      </div>
    </section>
  );
}
