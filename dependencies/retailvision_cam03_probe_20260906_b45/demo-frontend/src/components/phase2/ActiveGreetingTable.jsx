import { ChevronRight, PlaySquare } from "lucide-react";
import { attributionLabel, compactId, greetingLabel, greetingTone } from "../../lib/format";
import { DataAvailabilityNotice } from "../DataAvailabilityNotice";
import { DataSourceBadge } from "../DataSourceBadge";

export function ActiveGreetingTable({ onRetry, onSelect, source = { kind: "demo" }, tasks = [] }) {
  const hasTasks = tasks.length > 0;
  const canRenderTasks = source.kind === "demo" || source.kind === "live" || hasTasks;
  const activeCount = tasks.filter((task) => ["WAITING_FOR_GREETING", "GREETING_IN_PROGRESS"].includes(task.status)).length;
  return (
    <section className="panel active-greeting-panel" aria-labelledby="active-greeting-title">
      <div className="panel-heading panel-heading--table">
        <div>
          <div className="eyebrow"><span className="panel-kicker">服务任务</span><DataSourceBadge source={source} /></div>
          <h2 id="active-greeting-title">活跃迎宾任务</h2>
          <p>{canRenderTasks ? tasks.length + " 条任务 · 点击行查看服务时间线与证据" : "活跃任务需要 Runtime 的独立只读视图，不能由历史事件推断。"}</p>
        </div>
        <span className="count-badge">{canRenderTasks ? activeCount + " 进行中" : "未提供"}</span>
      </div>

      {!canRenderTasks ? <DataAvailabilityNotice onRetry={onRetry} source={source} title="活跃迎宾任务不可用" /> : (
        <div className="desktop-table-wrap">
          <table className="active-greeting-table">
            <thead><tr><th>顾客</th><th>会员</th><th>员工</th><th>状态</th><th className="numeric">等待</th><th className="numeric">距离</th><th className="numeric">移动</th><th className="numeric">置信度</th><th>证据</th></tr></thead>
            <tbody>
              {tasks.length ? tasks.map((task) => (
                <tr key={task.id}>
                  <td><button className="table-link" onClick={() => onSelect(task)} type="button">{task.customer}<ChevronRight aria-hidden="true" size={14} /></button></td>
                  <td>{task.member}</td>
                  <td><div className="staff-cell"><span>{task.staff}</span><small className={task.attribution === "unknown" ? "attribution--unknown" : ""}>{attributionLabel(task.attribution)}</small><small className="staff-cell__track mono" title={task.staffTrack}>{compactId(task.staffTrack)}</small></div></td>
                  <td><span className={"status-badge status-badge--" + greetingTone(task.status)}>{greetingLabel(task.status)}</span></td>
                  <td className="numeric mono">{task.wait}</td><td className="numeric">{task.distance}</td><td className="numeric">{task.travel}</td><td className="numeric">{task.confidence || "未提供"}</td>
                  <td><button aria-label={task.id + " 的证据详情"} className="evidence-link" onClick={() => onSelect(task)} title="查看证据详情" type="button"><PlaySquare aria-hidden="true" size={15} />{task.evidence}</button></td>
                </tr>
              )) : <tr><td className="empty-table-cell" colSpan="9">当前没有等待中的迎宾任务。</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
