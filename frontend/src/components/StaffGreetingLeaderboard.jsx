import { useMemo, useState } from "react";
import { ArrowDownUp, Info, UserRound } from "lucide-react";
import { attributionLabel, formatDistance, formatResponse } from "../lib/format";
import { DataAvailabilityNotice } from "./DataAvailabilityNotice";
import { DataSourceBadge } from "./DataSourceBadge";

const sortableColumns = [
  { key: "greetingCount", label: "今日迎宾" },
  { key: "successRate", label: "成功率" },
  { key: "averageResponseSeconds", label: "平均响应" },
];

function sortDirection(sort, key) {
  if (sort.key !== key) return "none";
  return sort.direction === "asc" ? "ascending" : "descending";
}

export function StaffGreetingLeaderboard({ onRetry, onSelect, onSort, sort, source = { kind: "demo" }, staff = [] }) {
  const [filters, setFilters] = useState({
    date: "2026-07-30",
    range: "today",
    shift: "late",
    staffId: "all",
    store: "STORE-SH-001",
  });
  const visibleStaff = useMemo(() => filters.staffId === "all" ? staff : staff.filter((entry) => entry.staffId === filters.staffId), [filters.staffId, staff]);
  const staffOptions = staff.filter((entry) => entry.staffId);
  const available = source.kind === "demo" || staff.length > 0;

  function updateFilter(key, value) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  return (
    <section className="panel staff-leaderboard-panel" aria-labelledby="staff-leaderboard-title">
      <div className="panel-heading panel-heading--table">
        <div>
          <div className="eyebrow"><span className="panel-kicker">Staff Greeting Performance</span><DataSourceBadge source={source} /></div>
          <h2 id="staff-leaderboard-title">员工服务表现</h2>
          <p>个人次数仅统计已确认、具备 staff_id 且已识别或人工绑定的服务周期。</p>
        </div>
      </div>
      {!available ? <DataAvailabilityNotice onRetry={onRetry} source={source} title="员工迎宾排行不可用" /> : <>
        <div className="leaderboard-filters" aria-label="员工统计筛选">
          <label className="range-select"><span>统计范围</span><select aria-label="统计范围" onChange={(event) => updateFilter("range", event.target.value)} value={filters.range}><option value="today">今日</option><option value="yesterday">昨日</option><option value="week">近 7 天</option><option value="custom">自定义日期</option></select></label>
          {filters.range === "custom" && <label className="range-select"><span>日期</span><input aria-label="自定义日期" onChange={(event) => updateFilter("date", event.target.value)} type="date" value={filters.date} /></label>}
          <label className="range-select"><span>门店</span><select aria-label="门店" onChange={(event) => updateFilter("store", event.target.value)} value={filters.store}><option value="STORE-SH-001">虹桥旗舰店</option></select></label>
          <label className="range-select"><span>班次</span><select aria-label="班次" onChange={(event) => updateFilter("shift", event.target.value)} value={filters.shift}><option value="late">晚班 14:00 - 22:00</option><option value="all">全部班次</option></select></label>
          <label className="range-select"><span>员工</span><select aria-label="员工" onChange={(event) => updateFilter("staffId", event.target.value)} value={filters.staffId}><option value="all">全部已绑定员工</option>{staffOptions.map((entry) => <option key={entry.staffId} value={entry.staffId}>{entry.staffName}</option>)}</select></label>
          <span className="leaderboard-filters__note">DEMO：范围、门店和班次为静态样本上下文；员工筛选已生效。</span>
        </div>
        <div className="desktop-table-wrap">
          <table className="staff-leaderboard">
            <thead><tr><th>排名</th><th>员工</th>{sortableColumns.map((column) => <th aria-sort={sortDirection(sort, column.key)} className="numeric" key={column.key}><button className="sort-button" onClick={() => onSort(column.key)} type="button">{column.label}<ArrowDownUp aria-hidden="true" size={13} strokeWidth={1.8} /></button></th>)}<th className="numeric">平均距离</th><th aria-sort={sortDirection(sort, "lastGreetingAt")} className="numeric"><button className="sort-button" onClick={() => onSort("lastGreetingAt")} type="button">最后迎宾<ArrowDownUp aria-hidden="true" size={13} strokeWidth={1.8} /></button></th></tr></thead>
            <tbody>{visibleStaff.map((entry, index) => <tr className={!entry.staffId ? "staff-row--unbound" : ""} key={entry.staffId || entry.staffName}>
              <td className="rank-cell">{entry.staffId ? index + 1 : "—"}</td>
              <td><button className="staff-name-button" disabled={!entry.staffId} onClick={() => onSelect(entry)} type="button"><span className="staff-avatar"><UserRound aria-hidden="true" size={14} /></span><span><strong>{entry.staffName}</strong><small>{entry.staffId || "仅 staff_track_token"}</small></span></button><span className={"attribution-badge attribution-badge--" + entry.attribution}>{attributionLabel(entry.attribution)}</span></td>
              <td className="numeric tabular">{entry.greetingCount}</td><td className="numeric tabular">{entry.successRate.toFixed(1)}%</td><td className="numeric tabular">{formatResponse(entry.averageResponseSeconds)}</td><td className="numeric tabular">{formatDistance(entry.averageDistanceMetres)}</td><td className="numeric tabular">{entry.lastGreetingAt}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <div className="leaderboard-note"><Info aria-hidden="true" size={15} strokeWidth={1.8} /><span>成功率 = confirmed / (confirmed + not_observed)；未绑定员工仅进入门店汇总，不进入个人排名。</span></div>
      </>}
    </section>
  );
}
