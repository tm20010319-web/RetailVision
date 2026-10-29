import {
  activeGreetingTasks,
  demoEvents,
  healthServices,
  knownCustomer,
  operationsMetrics,
  qualitySummary,
  staffGreetingStats,
  storeContext,
  trendData,
} from "../data/operationsDemo";

const UNAVAILABLE_METRICS = [
  ["今日迎宾总数", "缺少 dashboard summary 接口"],
  ["确认迎宾", "缺少按日确认迎宾接口"],
  ["未观察到", "缺少按日评估统计接口"],
  ["平均响应时间", "当前接口未提供统一口径"],
  ["在线员工", "当前接口未提供 Staff Role 在线态"],
  ["未绑定员工事件", "当前接口未提供 Staff Track Binding"],
];

const sourceLabels = {
  poll: "设备轮询",
  push: "设备识别推送",
};

const assessmentLabels = {
  confirmed: "已确认",
  not_observed: "未观察到",
  uncertain: "不确定",
};

const evidenceLabels = {
  failed: "生成失败",
  pending: "待生成",
  processing: "生成中",
  ready: "已就绪",
  requested: "待生成",
};

function arrayOf(value) {
  return Array.isArray(value) ? value : [];
}

function isPresent(value) {
  return value !== null && value !== undefined && value !== "";
}

function asNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function compactValue(value, maxLength = 14) {
  const text = String(value || "");
  if (!text) return "—";
  if (text.length <= maxLength) return text;
  return text.slice(0, Math.max(4, maxLength - 5)) + "..." + text.slice(-4);
}

function dateValue(value) {
  const result = Date.parse(value || "");
  return Number.isFinite(result) ? result : 0;
}

export function displayDateTime(value) {
  if (!value) return "未提供";
  const timestamp = dateValue(value);
  if (!timestamp) return String(value).replace("T", " ").slice(0, 19);
  return new Intl.DateTimeFormat("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(timestamp));
}

export function displayTime(value) {
  if (!value) return "—";
  const timestamp = dateValue(value);
  if (!timestamp) return String(value).replace("T", " ").slice(11, 16) || "—";
  return new Intl.DateTimeFormat("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(timestamp));
}

function displayPercent(value) {
  const number = asNumber(value);
  if (number === null) return "未提供";
  return (number <= 1 ? number * 100 : number).toFixed(1) + "%";
}

function displayMetres(value) {
  const number = asNumber(value);
  return number === null ? "未提供" : number.toFixed(2) + " m";
}

function displaySeconds(value) {
  const number = asNumber(value);
  return number === null ? "未提供" : number.toFixed(1) + " s";
}

function statusTone(value, type) {
  const state = String(value || "").toLowerCase();
  if (["online", "healthy", "running", "idle", "ready", "processed"].includes(state)) return "normal";
  if (["stale", "busy", "backlogged", "pending", "processing", "uncertain"].includes(state)) return "warning";
  if (["failed", "error"].includes(state)) return "error";
  if (type === "profile" && state === "disabled") return "warning";
  return "unknown";
}

function serviceDetail({ id, label, raw, status }) {
  const provided = raw && typeof raw === "object";
  const rawState = raw?.state;
  return {
    id,
    label,
    state: provided ? statusTone(rawState, id) : "unknown",
    compact: provided ? (rawState || "未提供") : "未提供",
    heartbeat: displayDateTime(raw?.last_seen_at || raw?.observed_at || status?.generated_at),
    latency: "未提供",
    version: "未提供",
    camera: status?.device_id || "未提供",
    error: provided ? (raw?.last_error || "无") : "当前接口未提供",
  };
}

export function buildHealthServices(status) {
  return [
    serviceDetail({ id: "api", label: "API", raw: status?.api, status }),
    serviceDetail({ id: "push", label: "Push", raw: status?.push, status }),
    serviceDetail({ id: "poll", label: "Poll", raw: status?.poll, status }),
    serviceDetail({ id: "identity", label: "Identity Runtime", raw: status?.runtime, status }),
    serviceDetail({ id: "profile", label: "画像 Worker", raw: status?.profile_worker, status }),
    serviceDetail({ id: "behavior", label: "Behavior Runtime", raw: null, status }),
    serviceDetail({ id: "face", label: "人脸识别", raw: null, status }),
    serviceDetail({ id: "vest", label: "蓝马甲模型", raw: null, status }),
    serviceDetail({ id: "evidence", label: "Evidence Worker", raw: null, status }),
  ];
}

function sourceFor(resource, unavailableMessage) {
  if (resource?.data !== null && resource?.data !== undefined) {
    return {
      kind: "live",
      message: resource.status === "stale" ? "正在保留最近一次成功数据，后台刷新失败。" : "",
      stale: resource.status === "stale",
    };
  }
  if (resource?.status === "loading" || resource?.status === "refreshing" || resource?.status === "disabled") {
    return { kind: "loading", message: "正在请求只读运营接口。" };
  }
  if (resource?.status === "error") {
    return { kind: "error", message: "运营 API 请求失败。请检查服务状态、服务地址、网络和 CORS 配置后重试。" };
  }
  return { kind: "unavailable", message: unavailableMessage };
}

function mergeSource(resources, unavailableMessage) {
  const available = resources.filter((resource) => resource?.data !== null && resource?.data !== undefined);
  if (available.length) return { kind: "live", stale: available.some((resource) => resource.status === "stale") };
  if (resources.some((resource) => resource?.status === "error")) {
    return { kind: "error", message: "无法加载事件数据。请检查运营 API 后重试。" };
  }
  if (resources.some((resource) => ["loading", "refreshing", "disabled"].includes(resource?.status))) {
    return { kind: "loading", message: "正在读取近期运营事件。" };
  }
  return { kind: "unavailable", message: unavailableMessage };
}

function highestRecentRecognition(items) {
  return [...arrayOf(items)]
    .filter((item) => item && item.member)
    .sort((left, right) => dateValue(right.occurred_at) - dateValue(left.occurred_at))[0] || null;
}

function newestRecognition(items) {
  return [...arrayOf(items)]
    .filter(Boolean)
    .sort((left, right) => dateValue(right.occurred_at) - dateValue(left.occurred_at))[0] || null;
}

function profileTags(profile) {
  return arrayOf(profile?.profile?.style_tags)
    .filter((value) => typeof value === "string" && value.trim())
    .slice(0, 5);
}

function profileRecommendation(profile) {
  const data = profile?.profile;
  if (!data || typeof data !== "object") return "当前画像接口未提供可用服务建议。";
  const value = data.service_recommendation || data.recommendation || data.service_advice || data.summary;
  return typeof value === "string" && value.trim()
    ? value.trim()
    : "当前画像仅提供标签，尚未提供服务建议。";
}

export function buildCurrentCustomer({ memberBundle, recognition, recognitionSource }) {
  if (!recognition) {
    if (recognitionSource.kind === "live") {
      return {
        customer: null,
        source: { kind: "unavailable", label: "暂无事件", message: "最近识别接口已返回，但当前没有可展示的识别事件。" },
      };
    }
    return { customer: null, source: recognitionSource };
  }

  if (!recognition.member) {
    return {
      customer: {
        kind: "anonymous",
        track: isPresent(recognition.track_id) ? "本次识别 Track #" + recognition.track_id + "（非身份标识）" : "本事件未提供 Track",
        behavior: "当前行为接口未提供",
        state: recognition.status === "waiting_mapping" ? "等待会员映射" : "未关联会员",
      },
      source: { kind: "live" },
    };
  }

  const member = memberBundle?.member || recognition.member;
  const visits = arrayOf(memberBundle?.visits?.visits);
  return {
    customer: {
      kind: "member",
      name: member.name || recognition.member.name || "已识别会员",
      id: member.id || recognition.member.id || "未提供",
      tier: member.tier || recognition.member.tier || "未提供",
      points: isPresent(member.points) ? Number(member.points).toLocaleString("zh-CN") : "未提供",
      visits: isPresent(member.visit_count) ? member.visit_count : "未提供",
      lastVisit: member.last_visit ? displayDateTime(member.last_visit) : (visits[0]?.entry_time ? displayDateTime(visits[0].entry_time) : "未提供"),
      visitId: recognition.visit_id || "未提供",
      source: sourceLabels[recognition.source] || recognition.source || "未提供",
      confidence: displayPercent(recognition.similarity),
      track: isPresent(recognition.track_id) ? "本次识别 Track #" + recognition.track_id + "（非身份标识）" : "本事件未提供 Track",
      behavior: "当前行为接口未提供",
      tags: profileTags(memberBundle?.profile),
      recommendation: profileRecommendation(memberBundle?.profile),
      profileState: memberBundle?.profile?.status || "not_started",
    },
    source: { kind: "live" },
  };
}

function behaviorType(event) {
  if (event.event_type === "staff_greeting") {
    if (event.assessment === "confirmed") return { category: "greeting", type: "Greeting Confirmed" };
    if (event.assessment === "not_observed") return { category: "exception", type: "Greeting Not Observed" };
    return { category: "exception", type: "Greeting Uncertain" };
  }
  if (event.event_type === "merchandise_dwell") return { category: "product", type: "Region Dwell" };
  if (event.event_type === "merchandise_reach") return { category: "product", type: "Reach" };
  return { category: "exception", type: event.event_type || "Behavior Event" };
}

function evidenceView(rawEvidence, refreshedEvidence, getEvidenceVideoUrl) {
  if (!rawEvidence?.id) return { available: false, source: "live", status: "无证据" };
  const evidence = refreshedEvidence || rawEvidence;
  const available = Boolean(evidence.available);
  return {
    available,
    id: rawEvidence.id,
    source: "live",
    status: evidenceLabels[evidence.status] || evidence.status || "状态未知",
    videoUrl: available ? getEvidenceVideoUrl(rawEvidence.id) : null,
  };
}

function recognitionEvent(event) {
  const hasMember = Boolean(event.member);
  return {
    camera: event.device_id || "未提供",
    category: "member",
    confidence: displayPercent(event.similarity),
    evidence: { available: false, source: "live", status: "无证据" },
    facts: [
      { label: "识别事件", value: compactValue(event.event_id), mono: true },
      { label: "识别来源", value: sourceLabels[event.source] || event.source || "未提供" },
      { label: "处理状态", value: event.status || "未提供" },
      { label: "关联 Visit", value: event.visit_id || "未提供", mono: true },
    ],
    id: "recognition:" + event.event_id,
    kind: "event",
    occurredAt: event.occurred_at,
    reference: "R " + compactValue(event.event_id),
    state: hasMember ? "已识别" : (event.reason || event.status || "待处理"),
    subject: hasMember ? (event.member.name || "已识别会员") : "未关联会员的识别事件",
    time: displayTime(event.occurred_at),
    trackLabel: isPresent(event.track_id) ? "Track #" + event.track_id + "（本事件）" : null,
    type: hasMember ? "Member Identified" : "Identity Resolution",
    visitId: event.visit_id || null,
  };
}

function entryEvent(event) {
  const isMember = Boolean(event.member_id);
  return {
    camera: event.camera_id || "未提供",
    category: "entry",
    confidence: displayPercent(event.confidence),
    evidence: { available: false, source: "live", status: "无证据" },
    facts: [
      { label: "进店事件", value: compactValue(event.id), mono: true },
      { label: "身份状态", value: event.identity_status || "anonymous" },
      { label: "访客类型", value: event.visitor_type || "anonymous" },
      { label: "会员 ID", value: event.member_id || "未关联" , mono: Boolean(event.member_id) },
    ],
    id: "traffic:" + event.id,
    kind: "event",
    occurredAt: event.occurred_at,
    reference: "E " + compactValue(event.id),
    state: isMember ? "已关联会员" : "匿名顾客",
    subject: isMember ? "会员 " + compactValue(event.member_id) : "匿名顾客进店",
    time: displayTime(event.occurred_at),
    trackLabel: isPresent(event.track_id) ? "Track #" + event.track_id + "（本事件）" : null,
    type: "Customer Entry",
    visitId: null,
  };
}

function behaviorEvent(event, evidenceById, getEvidenceVideoUrl) {
  const type = behaviorType(event);
  const details = event.details && typeof event.details === "object" ? event.details : {};
  const isGreeting = event.event_type === "staff_greeting";
  const isDemo = Boolean(event.is_demo) || details.greeting_profile === "demo_relaxed";
  const mockAttribution = event.staff_attribution_source === "configured_mock";
  const greetingSubject = event.staff_name
    ? (mockAttribution ? "演示归属：" + event.staff_name : "迎宾归属：" + event.staff_name)
    : "员工身份未绑定";
  const evidence = evidenceView(event.evidence, evidenceById?.[event.evidence?.id], getEvidenceVideoUrl);
  const responseFact = isDemo
    ? {
      label: "Demo 响应耗时",
      value: isPresent(details.demo_response_seconds)
        ? displaySeconds(details.demo_response_seconds) + "（稳定配对至确认）"
        : "旧版事件，未记录",
    }
    : {
      label: "响应时间",
      value: displaySeconds(details.response_seconds) + "（进入 greeting_area 至迎宾确认）",
    };
  const calibrationFact = details.calibration_status === "provisional"
    && details.distance_source === "ground_homography"
    ? "临时标定 · 地面单应矩阵"
    : "标定不可用";
  return {
    camera: event.camera_id || "未提供",
    category: type.category,
    confidence: displayPercent(event.confidence),
    demo: isDemo,
    evidence,
    facts: [
      { label: "行为事件", value: compactValue(event.id), mono: true },
      { label: "评估", value: assessmentLabels[event.assessment] || event.assessment || "未提供" },
      { label: "会员 ID", value: event.member_id || "未关联", mono: Boolean(event.member_id) },
      ...(isGreeting ? [
        ...(isDemo ? [{ label: "数据范围", value: "DEMO（不计正式 KPI）" }] : []),
        { label: "迎宾归属", value: event.staff_name ? (mockAttribution ? event.staff_name + "（配置的演示归属，非 Staff Identity）" : event.staff_name) : "未绑定员工" },
        responseFact,
        { label: "最小距离", value: displayMetres(details.minimum_distance_metres) },
        { label: "员工移动", value: displayMetres(details.staff_travel_metres) },
        ...(isDemo ? [{ label: "距离标定", value: calibrationFact }] : []),
      ] : []),
    ],
    id: "behavior:" + event.id,
    kind: "event",
    occurredAt: event.started_at || event.ended_at,
    reference: "B " + compactValue(event.id),
    state: assessmentLabels[event.assessment] || event.assessment || "未提供",
    subject: isGreeting ? greetingSubject : (event.area_id ? "区域 " + event.area_id : "商品行为对象未提供"),
    time: displayTime(event.started_at || event.ended_at),
    trackLabel: null,
    type: type.type,
    visitId: null,
  };
}

function shadowEvaluationEvent(item, getEvidenceVideoUrl) {
  const evaluation = item?.evaluation || {};
  const cycle = item?.cycle || {};
  const metrics = evaluation.raw_metrics && typeof evaluation.raw_metrics === "object" ? evaluation.raw_metrics : {};
  const path = evaluation.candidate_path || "未形成候选路径";
  const decisionLabels = {
    would_confirm: "模拟确认",
    uncertain: "不确定",
    not_observed: "未观察到",
    cancelled: "已取消",
  };
  const evidence = item?.evidence?.id
    ? {
      available: Boolean(item.evidence.available),
      id: item.evidence.id,
      source: "live",
      status: evidenceLabels[item.evidence.status] || item.evidence.status || "状态未知",
      videoUrl: item.evidence.available ? getEvidenceVideoUrl(item.evidence.id) : null,
    }
    : { available: false, source: "live", status: "无证据" };
  const v1 = item?.v1;
  return {
    camera: cycle.camera_id || "未提供",
    category: "shadow",
    confidence: displayPercent(evaluation.interaction_score),
    evidence,
    facts: [
      { label: "结果标识", value: "SHADOW / 不计绩效" },
      { label: "候选路径", value: path },
      { label: "V2 决策", value: decisionLabels[evaluation.decision] || evaluation.decision || "未提供" },
      { label: "V1 对照", value: v1 ? (v1.decision || "未提供") : "未产生正式事件" },
      { label: "原始近距离", value: displaySeconds(metrics.raw_proximity_seconds) },
      { label: "距离来源", value: metrics.distance_source || evaluation.calibration_mode || "未提供" },
      { label: "标定一致性", value: evaluation.calibration_consistency || "未提供" },
      { label: "取消原因", value: evaluation.reason || cycle.cancel_reason || "无" },
    ],
    id: "shadow:" + item.id,
    kind: "event",
    occurredAt: evaluation.settled_at || cycle.settled_at,
    reference: "S " + compactValue(item.id),
    state: decisionLabels[evaluation.decision] || evaluation.decision || "未提供",
    subject: "V2 Shadow · " + path,
    time: displayTime(evaluation.settled_at || cycle.settled_at),
    trackLabel: cycle.customer_track_token ? "Customer " + compactValue(cycle.customer_track_token) : null,
    token: cycle.customer_track_token || null,
    type: "V2 Shadow Greeting",
    visitId: null,
    shadow: true,
  };
}

export function buildRecentEvents({ behaviors, entries, evidenceById, getEvidenceVideoUrl, recognitions }) {
  const events = [
    ...arrayOf(recognitions).map(recognitionEvent),
    ...arrayOf(entries).map(entryEvent),
    ...arrayOf(behaviors).map((event) => behaviorEvent(event, evidenceById, getEvidenceVideoUrl)),
  ];
  const deduped = new Map();
  events.forEach((event) => deduped.set(event.id, event));
  return [...deduped.values()].sort((left, right) => {
    const difference = dateValue(right.occurredAt) - dateValue(left.occurredAt);
    return difference || left.id.localeCompare(right.id);
  });
}

export function buildShadowEvents({ evaluations, getEvidenceVideoUrl }) {
  return arrayOf(evaluations)
    .map((item) => shadowEvaluationEvent(item, getEvidenceVideoUrl))
    .sort((left, right) => dateValue(right.occurredAt) - dateValue(left.occurredAt));
}

function unavailableMetrics() {
  return UNAVAILABLE_METRICS.map(([label, detail]) => ({
    detail,
    label,
    tone: "neutral",
    trend: "未提供",
    value: "—",
  }));
}

export function buildLegacyGreetingSummary(items) {
  const rows = arrayOf(items)
    .filter((item) => item && item.staff_name)
    .map((item) => ({
      greetingCount: asNumber(item.greeting_count) || 0,
      lastGreetingAt: displayDateTime(item.last_greeted_at),
      staffName: item.staff_name,
    }));
  return {
    total: rows.reduce((sum, item) => sum + item.greetingCount, 0),
    rows,
  };
}

function liveStoreContext(status, storeConfig) {
  return {
    cameraOnline: "未提供",
    code: storeConfig?.code || "未配置",
    name: storeConfig?.name || "未配置门店",
    shift: storeConfig?.shift || "班次接口未提供",
    time: status?.generated_at ? displayDateTime(status.generated_at) : "等待系统状态",
    timeIso: status?.generated_at || null,
  };
}

export function buildLiveDashboard({
  behaviorResource,
  evidenceResource,
  getEvidenceVideoUrl,
  memberResource,
  recognitionResource,
  shadowResource,
  demoStaffSummaryResource,
  staffSummaryResource,
  statusResource,
  storeConfig,
  trafficResource,
}) {
  const recognitions = arrayOf(recognitionResource.data?.items);
  const entries = arrayOf(trafficResource.data?.items);
  const behaviors = arrayOf(behaviorResource.data?.items);
  const shadowEvaluations = arrayOf(shadowResource?.data?.items);
  const currentRecognition = highestRecentRecognition(recognitions) || newestRecognition(recognitions);
  const recognitionSource = sourceFor(recognitionResource, "当前没有可用的近期识别接口数据。");
  const currentCustomer = buildCurrentCustomer({
    memberBundle: memberResource.data,
    recognition: currentRecognition,
    recognitionSource,
  });
  const eventSource = mergeSource(
    [recognitionResource, trafficResource, behaviorResource],
    "当前没有可用的近期进店或行为事件接口数据。",
  );
  const legacySummary = buildLegacyGreetingSummary(staffSummaryResource.data?.items);
  const demoGreetingSummary = buildLegacyGreetingSummary(
    demoStaffSummaryResource?.data?.items,
  );
  const legacySummarySource = sourceFor(
    staffSummaryResource,
    "当前接口未提供可用于个人员工排行的 Staff Greeting Statistics。",
  );
  const evidenceById = evidenceResource.data || {};

  return {
    activeGreetingTasks: [],
    cameraSource: {
      kind: "unavailable",
      message: "当前后端未提供实时视频帧或 Overlay 只读接口。需要独立的 camera live-view 契约。",
    },
    currentCustomer: currentCustomer.customer,
    currentCustomerSource: currentCustomer.source,
    events: buildRecentEvents({
      behaviors,
      entries,
      evidenceById,
      getEvidenceVideoUrl,
      recognitions,
    }),
    shadowEvents: buildShadowEvents({
      evaluations: shadowEvaluations,
      getEvidenceVideoUrl,
    }),
    healthServices: buildHealthServices(statusResource.data),
    legacyGreetingSummary: legacySummary,
    demoGreetingSummary,
    metrics: unavailableMetrics(),
    mode: "live",
    qualitySummary: [],
    sources: {
      activeGreetings: {
        kind: "unavailable",
        message: "当前后端未提供活跃迎宾任务接口。历史 Behavior Event 不能被伪装为等待中的服务任务。",
      },
      events: eventSource,
      shadow: sourceFor(
        shadowResource,
        "当前没有 V2 Shadow 评估数据；正式事件流未混入 Shadow 结果。",
      ),
      health: sourceFor(statusResource, "当前没有可用的系统状态接口数据。"),
      metrics: {
        kind: "unavailable",
        message: "当前后端未提供按日汇总、成功率、响应时间和未绑定员工统计接口。",
      },
      quality: legacySummarySource.kind === "live" && legacySummary.rows.length
        ? { kind: "mock_attribution", message: "下方仅为历史 confirmed 迎宾的 configured_mock 归属，不是员工绩效。" }
        : {
          kind: legacySummarySource.kind,
          message: legacySummarySource.message || "当前无可展示的历史迎宾归属。",
        },
      demoGreetings: sourceFor(
        demoStaffSummaryResource,
        "当前没有 demo_relaxed 迎宾记录。",
      ),
      staff: {
        kind: "unavailable",
        message: "当前接口没有 staff_id、身份归属、日期范围、成功率或响应时间，不能生成员工排行榜。",
      },
      trend: {
        kind: "unavailable",
        message: "当前后端未提供按小时聚合的客流与确认迎宾趋势接口。",
      },
    },
    staff: [],
    store: liveStoreContext(statusResource.data, storeConfig),
    trend: null,
  };
}

export function buildDemoDashboard() {
  const source = { kind: "demo" };
  return {
    activeGreetingTasks,
    cameraSource: source,
    currentCustomer: knownCustomer,
    currentCustomerSource: source,
    events: demoEvents,
    shadowEvents: [],
    healthServices,
    legacyGreetingSummary: null,
    demoGreetingSummary: null,
    metrics: operationsMetrics,
    mode: "demo",
    qualitySummary,
    sources: {
      activeGreetings: source,
      events: source,
      shadow: source,
      health: source,
      metrics: source,
      quality: source,
      demoGreetings: source,
      staff: source,
      trend: source,
    },
    staff: staffGreetingStats,
    store: storeContext,
    trend: trendData,
  };
}

export function buildUnavailableDashboard(storeConfig) {
  const source = { kind: "unavailable", message: "未启用运营 API。开发环境可显式设置 VITE_OPERATIONS_DATA_MODE=demo 查看演示数据。" };
  return {
    activeGreetingTasks: [],
    cameraSource: source,
    currentCustomer: null,
    currentCustomerSource: source,
    events: [],
    shadowEvents: [],
    healthServices: buildHealthServices(null),
    legacyGreetingSummary: null,
    demoGreetingSummary: null,
    metrics: unavailableMetrics(),
    mode: "unavailable",
    qualitySummary: [],
    sources: {
      activeGreetings: source,
      events: source,
      shadow: source,
      health: source,
      metrics: source,
      quality: source,
      demoGreetings: source,
      staff: source,
      trend: source,
    },
    staff: [],
    store: liveStoreContext(null, storeConfig),
    trend: null,
  };
}
