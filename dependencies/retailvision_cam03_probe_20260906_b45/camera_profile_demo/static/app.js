const state = {
  visitors: [],
  selectedId: null,
  inFlight: false,
};

const cameraStatus = document.getElementById("cameraStatus");
const modelStatus = document.getElementById("modelStatus");
const visitorCount = document.getElementById("visitorCount");
const visitorList = document.getElementById("visitorList");
const detailPanel = document.getElementById("detailPanel");

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const formatTime = (value) => {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
};

const statusLabel = (status) => ({
  pending: "等待分析",
  running: "大模型分析中",
  succeeded: "画像完成",
  fallback: "兜底结果",
}[status] || status);

function renderStatus(status) {
  cameraStatus.textContent = status.camera_connected ? "摄像头已连接" : "摄像头重连中";
  cameraStatus.className = `status-pill ${status.camera_connected ? "ok" : "warn"}`;
  if (status.worker_busy) {
    modelStatus.textContent = `模型分析中 · 队列 ${status.queue_depth}`;
  } else {
    modelStatus.textContent = `${status.ollama_model} · 待命`;
  }
  modelStatus.className = `status-pill ${status.worker_running ? "ok" : "warn"}`;
}

function renderList() {
  visitorCount.textContent = String(state.visitors.length);
  if (!state.visitors.length) {
    visitorList.innerHTML = '<div class="empty-list">等待真实摄像头抓拍</div>';
    return;
  }
  visitorList.innerHTML = state.visitors.map((visitor) => `
    <button class="visitor-item ${visitor.visitor_id === state.selectedId ? "active" : ""}"
      type="button" data-id="${escapeHtml(visitor.visitor_id)}">
      <img class="visitor-thumb" src="${escapeHtml(visitor.capture_url)}" alt="顾客抓拍" />
      <span class="visitor-copy">
        <span class="visitor-name">${escapeHtml(visitor.basics.display_name)}</span>
        <span class="visitor-time">${formatTime(visitor.captured_at)}</span>
        <span class="visitor-state">${escapeHtml(statusLabel(visitor.profile_status))}</span>
      </span>
    </button>
  `).join("");
  visitorList.querySelectorAll(".visitor-item").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedId = button.dataset.id;
      renderList();
      renderDetail();
    });
  });
}

const tags = (items) => (items || []).map(
  (item) => `<span class="tag">${escapeHtml(item)}</span>`
).join("") || '<span class="tag">暂无</span>';

function renderDetail() {
  const visitor = state.visitors.find((item) => item.visitor_id === state.selectedId);
  if (!visitor) {
    detailPanel.innerHTML = `
      <div class="empty-detail">
        <div class="empty-mark">AI</div>
        <h2>等待顾客进入画面</h2>
        <p>稳定 Track 生成后，真实抓拍与画像状态会显示在这里。</p>
      </div>`;
    return;
  }
  const basics = visitor.basics;
  const source = visitor.profile_source;
  const sourceBadge = source === "ollama"
    ? '<span class="source-badge ollama">真实 Ollama 画像</span>'
    : source === "mock_fallback"
      ? '<span class="source-badge fallback">演示兜底结果</span>'
      : "";
  const profile = visitor.profile;
  const profileHtml = profile ? `
    <div class="profile-content">
      <section class="profile-section">
        <h3>穿搭标签</h3><div class="tag-list">${tags(profile.style_tags)}</div>
      </section>
      <section class="profile-section">
        <h3>兴趣品类</h3><div class="tag-list">${tags(profile.interest_categories)}</div>
      </section>
      <section class="profile-section">
        <h3>可见服装</h3>
        <p>${escapeHtml(Object.entries(profile.visible_clothing || {}).map(([key, value]) => `${key}: ${value}`).join(" · ") || "无法判断")}</p>
      </section>
      <section class="profile-section">
        <h3>购买意向 / 置信度</h3>
        <p>${Math.round(profile.purchase_intent_score * 100)}% / ${Math.round(profile.confidence * 100)}%</p>
      </section>
      <section class="profile-section wide">
        <h3>画像摘要</h3><p>${escapeHtml(profile.profile_summary)}</p>
      </section>
      <section class="profile-section wide">
        <h3>导购建议</h3><p>${escapeHtml(profile.sales_suggestion)}</p>
      </section>
    </div>
    ${visitor.last_error ? `<div class="error-banner">模型异常：${escapeHtml(visitor.last_error)}，当前显示演示兜底结果。</div>` : ""}
  ` : `
    <div class="pending-block">
      <strong>${escapeHtml(statusLabel(visitor.profile_status))}</strong>
      <span>真实抓拍已进入单线程画像队列</span>
    </div>`;

  detailPanel.innerHTML = `
    <div class="detail-grid">
      <div class="capture-column">
        <img class="capture-image" src="${escapeHtml(visitor.capture_url)}" alt="真实摄像头人物抓拍" />
        <div class="capture-meta">
          <span>抓拍时间 ${formatTime(visitor.captured_at)}</span>
          <span>Track #${escapeHtml(visitor.track_id)} · 检测置信度 ${Math.round(visitor.detection_confidence * 100)}%</span>
          <span>${escapeHtml(visitor.camera_id)}</span>
        </div>
      </div>
      <div class="profile-column">
        <div class="profile-header">
          <div><p class="eyebrow">CUSTOMER PROFILE</p><h2>${escapeHtml(basics.display_name)}</h2></div>
          <div class="badge-row"><span class="job-badge">${escapeHtml(statusLabel(visitor.profile_status))}</span>${sourceBadge}</div>
        </div>
        <div class="basics">
          <div class="metric"><span>顾客等级</span><strong>${escapeHtml(basics.tier)}</strong></div>
          <div class="metric"><span>积分</span><strong>${escapeHtml(basics.points)}</strong></div>
          <div class="metric"><span>到店次数</span><strong>${escapeHtml(basics.visit_count)}</strong></div>
          <div class="metric"><span>顾客状态</span><strong>${escapeHtml(basics.customer_status)}</strong></div>
          <div class="metric"><span>访客编号</span><strong>${escapeHtml(visitor.visitor_id)}</strong></div>
        </div>
        ${profileHtml}
      </div>
    </div>`;
}

async function refresh() {
  if (state.inFlight) return;
  state.inFlight = true;
  try {
    const [statusResponse, visitorsResponse] = await Promise.all([
      fetch("/api/status", { cache: "no-store" }),
      fetch("/api/visitors", { cache: "no-store" }),
    ]);
    if (!statusResponse.ok || !visitorsResponse.ok) throw new Error("API unavailable");
    const status = await statusResponse.json();
    const payload = await visitorsResponse.json();
    state.visitors = payload.visitors || [];
    if (!state.selectedId || !state.visitors.some((item) => item.visitor_id === state.selectedId)) {
      state.selectedId = state.visitors[0]?.visitor_id || null;
    }
    renderStatus(status);
    renderList();
    renderDetail();
  } catch (_error) {
    cameraStatus.textContent = "服务连接异常";
    cameraStatus.className = "status-pill warn";
  } finally {
    state.inFlight = false;
  }
}

refresh();
setInterval(refresh, 1000);
