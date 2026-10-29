const labels = {
  demo: "DEMO DATA",
  error: "API 异常",
  live: "已接入 API",
  loading: "加载中",
  mock_attribution: "DEMO 归属",
  unavailable: "接口未提供",
};

export function DataSourceBadge({ source = { kind: "demo" } }) {
  const kind = source?.kind || "demo";
  return <span className={"data-source-badge data-source-badge--" + kind}>{source?.label || labels[kind] || labels.unavailable}</span>;
}
