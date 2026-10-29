import { ChevronRight, PlaySquare } from "lucide-react";
import { attributionLabel, compactId, greetingLabel, greetingTone } from "../lib/format";

export function ActiveGreetingTable({ onSelect, tasks }) {
  return (
    <section className="panel active-greeting-panel" aria-labelledby="active-greeting-title">
      <div className="panel-heading panel-heading--table">
        <div>
          <div className="eyebrow">
            <span className="panel-kicker">服务任务</span>
            <span className="demo-label">DEMO DATA</span>
          </div>
          <h2 id="active-greeting-title">活跃迎宾任务</h2>
          <p>{tasks.length} 条任务 · 点击行查看服务时间线与证据</p>
        </div>
        <span className="count-badge">{tasks.filter((task) => task.status !== "GREETED").length} 进行中</span>
      </div>

      <div className="desktop-table-wrap">
        <table className="active-greeting-table">
          <thead>
            <tr>
              <th>顾客</th>
              <th>会员</th>
              <th>员工</th>
              <th>状态</th>
              <th className="numeric">等待</th>
              <th className="numeric">距离</th>
              <th className="numeric">移动</th>
              <th>证据</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.id}>
                <td>
                  <button className="table-link" onClick={() => onSelect(task)} type="button">
                    {task.customer}
                    <ChevronRight aria-hidden="true" size={14} />
                  </button>
                </td>
                <td>{task.member}</td>
                <td>
                  <div className="staff-cell">
                    <span>{task.staff}</span>
                    <small className={task.attribution === "unknown" ? "attribution--unknown" : ""}>
                      {attributionLabel(task.attribution)}
                    </small>
                    <small className="staff-cell__track mono" title={task.staffTrack}>{compactId(task.staffTrack)}</small>
                  </div>
                </td>
                <td><span className={"status-badge status-badge--" + greetingTone(task.status)}>{greetingLabel(task.status)}</span></td>
                <td className="numeric mono">{task.wait}</td>
                <td className="numeric">{task.distance}</td>
                <td className="numeric">{task.travel}</td>
                <td>
                  <button
                    aria-label={task.id + " 的证据详情"}
                    className="evidence-link"
                    onClick={() => onSelect(task)}
                    title="查看证据详情"
                    type="button"
                  >
                    <PlaySquare aria-hidden="true" size={15} />
                    {task.evidence}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
