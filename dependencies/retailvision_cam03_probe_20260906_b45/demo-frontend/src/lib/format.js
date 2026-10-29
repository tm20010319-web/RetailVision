const greetingLabels = {
  WAITING_FOR_GREETING: "等待迎宾",
  GREETING_IN_PROGRESS: "迎宾中",
  GREETED: "已迎宾",
  NOT_OBSERVED: "未观察到",
  CANCELLED: "已取消",
};

const greetingTones = {
  WAITING_FOR_GREETING: "warning",
  GREETING_IN_PROGRESS: "info",
  GREETED: "positive",
  NOT_OBSERVED: "danger",
  CANCELLED: "neutral",
};

export function greetingLabel(value) {
  return greetingLabels[value] || value;
}

export function greetingTone(value) {
  return greetingTones[value] || "neutral";
}

export function compactId(value) {
  if (!value || value === "—") return "—";
  if (value.length <= 12) return value;
  return value.slice(0, 7) + "..." + value.slice(-4);
}

export function statusLabel(state) {
  const labels = {
    normal: "正常",
    warning: "警告",
    error: "异常",
    unknown: "未知",
  };
  return labels[state] || labels.unknown;
}

export function formatResponse(value) {
  return value === null || value === undefined ? "—" : value.toFixed(1) + " s";
}

export function formatDistance(value) {
  return value === null || value === undefined ? "—" : value.toFixed(2) + " m";
}

export function attributionLabel(value) {
  const labels = {
    identified: "已识别",
    manual_bound: "人工绑定",
    unknown: "未绑定",
    ambiguous: "待确认",
  };
  return labels[value] || "未绑定";
}
