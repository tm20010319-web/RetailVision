function linePoints(values, width, height, padding, maxValue) {
  const usableWidth = width - padding * 2;
  const usableHeight = height - padding * 2;
  return values.map((value, index) => {
    const x = padding + (usableWidth / (values.length - 1)) * index;
    const y = height - padding - (value / maxValue) * usableHeight;
    return x.toFixed(1) + "," + y.toFixed(1);
  }).join(" ");
}

export function TrendChart({ data }) {
  const width = 1040;
  const height = 220;
  const padding = 28;
  const maxValue = Math.max(...data.traffic, ...data.greetings) + 6;
  const trafficPoints = linePoints(data.traffic, width, height, padding, maxValue);
  const greetingPoints = linePoints(data.greetings, width, height, padding, maxValue);

  return (
    <section className="panel trend-panel" aria-labelledby="trend-chart-title">
      <div className="panel-heading panel-heading--chart">
        <div>
          <div className="eyebrow">
            <span className="panel-kicker">运营趋势</span>
            <span className="demo-label">DEMO DATA</span>
          </div>
          <h2 id="trend-chart-title">今日客流与迎宾趋势</h2>
          <p>按小时聚合的已确认进店与确认迎宾次数。</p>
        </div>
        <div className="chart-legend" aria-label="图表图例">
          <span><i className="chart-legend__line chart-legend__line--traffic" />进店客流</span>
          <span><i className="chart-legend__line chart-legend__line--greeting" />确认迎宾</span>
        </div>
      </div>
      <div className="chart-shell">
        <svg
          aria-label="今日客流和确认迎宾趋势：16 点达到峰值，之后平稳。"
          className="trend-chart"
          role="img"
          viewBox={"0 0 " + width + " " + height}
        >
          <title>今日客流和确认迎宾趋势</title>
          {[0.25, 0.5, 0.75].map((ratio) => (
            <line
              key={ratio}
              stroke="currentColor"
              strokeDasharray="4 5"
              x1={padding}
              x2={width - padding}
              y1={height - padding - (height - padding * 2) * ratio}
              y2={height - padding - (height - padding * 2) * ratio}
            />
          ))}
          <polyline className="trend-chart__line trend-chart__line--traffic" points={trafficPoints} />
          <polyline className="trend-chart__line trend-chart__line--greeting" points={greetingPoints} />
          {data.traffic.map((value, index) => {
            const points = trafficPoints.split(" ")[index].split(",");
            return <circle className="trend-chart__point trend-chart__point--traffic" cx={points[0]} cy={points[1]} key={"traffic-" + value + index} r="3.5" />;
          })}
          {data.greetings.map((value, index) => {
            const points = greetingPoints.split(" ")[index].split(",");
            return <circle className="trend-chart__point trend-chart__point--greeting" cx={points[0]} cy={points[1]} key={"greeting-" + value + index} r="3.5" />;
          })}
          {data.labels.map((label, index) => {
            const x = padding + ((width - padding * 2) / (data.labels.length - 1)) * index;
            return <text className="trend-chart__label" key={label} x={x} y={height - 5}>{label}</text>;
          })}
        </svg>
      </div>
      <p className="chart-summary">高峰出现在 16:00：34 次进店，29 次确认迎宾；迎宾转化保持稳定。</p>
    </section>
  );
}
