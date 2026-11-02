import { useEffect, useMemo, useState } from "react";
import { ActiveGreetingTable } from "./components/ActiveGreetingTable";
import { CurrentCustomerPanel } from "./components/CurrentCustomerPanel";
import { EventDetailDrawer } from "./components/EventDetailDrawer";
import { LiveCameraPanel } from "./components/LiveCameraPanel";
import { OperationsMetricRow } from "./components/OperationsMetricRow";
import { RealtimeEventTimeline } from "./components/RealtimeEventTimeline";
import { ServiceQualitySummary } from "./components/ServiceQualitySummary";
import { StaffGreetingLeaderboard } from "./components/StaffGreetingLeaderboard";
import { StoreHealthBar } from "./components/StoreHealthBar";
import { TrendChart } from "./components/TrendChart";
import { StaffGreetingDetailDrawer } from "./components/StaffGreetingDetailDrawer";
import { StaffTrackBindingDialog } from "./components/StaffTrackBindingDialog";
import { DemoGreetingControl } from "./components/DemoGreetingControl";
import { demoGreetingApi } from "./api/demoGreetingApi";
import { anonymousCustomer, knownCustomer } from "./data/operationsDemo";
import { useDemoGreetingControl } from "./hooks/useDemoGreetingControl";
import { useOperationsDashboard } from "./operationsDashboard";
import "./components/phase1-gap-fixes.css";
import "./components/demoGreetingControl.css";
import "./phase2.css";

function sortEntries(entries, sort) {
  const withPosition = entries.map((entry, position) => ({ entry, position }));
  const direction = sort.direction === "asc" ? 1 : -1;
  return withPosition.sort((left, right) => {
    if (!left.entry.staffId) return 1;
    if (!right.entry.staffId) return -1;
    const leftValue = left.entry[sort.key];
    const rightValue = right.entry[sort.key];
    if (typeof leftValue === "string") return leftValue.localeCompare(rightValue) * direction;
    return (leftValue - rightValue) * direction;
  }).map((item) => item.entry);
}

export default function App({ apiClient, dataMode = (import.meta.env.MODE === "test" || import.meta.env.VITEST) ? "demo" : undefined, demoGreetingClient = demoGreetingApi }) {
  const { hasErrors, refresh, snapshot: dashboard } = useOperationsDashboard({ adapter: apiClient, mode: dataMode });
  const demoGreeting = useDemoGreetingControl({
    api: demoGreetingClient,
    enabled: dashboard.mode === "live" && import.meta.env.MODE !== "test" && !import.meta.env.VITEST,
  });
  const [theme, setTheme] = useState("light");
  const [cameraMode, setCameraMode] = useState("live");
  const [customerMode, setCustomerMode] = useState("member");
  const [openHealthId, setOpenHealthId] = useState(null);
  const [activeFilter, setActiveFilter] = useState("all");
  const [sort, setSort] = useState({ key: "greetingCount", direction: "desc" });
  const [drawer, setDrawer] = useState(null);
  const [bindingTask, setBindingTask] = useState(null);
  const [toast, setToast] = useState("");
  const [overlays, setOverlays] = useState({ boxes: true, zones: true, paths: true, world: false, debug: false });

  const currentCustomer = dashboard.mode === "demo" ? (customerMode === "member" ? knownCustomer : anonymousCustomer) : dashboard.currentCustomer;
  const filteredEvents = useMemo(() => (
    activeFilter === "shadow"
      ? dashboard.shadowEvents
      : dashboard.events.filter((event) => activeFilter === "all" || event.category === activeFilter)
  ), [activeFilter, dashboard.events, dashboard.shadowEvents]);
  const sortedStaff = useMemo(() => sortEntries(dashboard.staff, sort), [dashboard.staff, sort]);

  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== "Escape") return;
      setOpenHealthId(null);
      setDrawer(null);
      setBindingTask(null);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(""), 3600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  function toggleOverlay(id) {
    setOverlays((current) => ({ ...current, [id]: !current[id] }));
  }

  function handleSort(key) {
    setSort((current) => ({ key, direction: current.key === key && current.direction === "desc" ? "asc" : "desc" }));
  }

  function openDrawer(kind, item) {
    setOpenHealthId(null);
    setDrawer({ kind, item });
  }

  function openBinding(task) {
    setDrawer(null);
    setBindingTask(task);
  }

  function confirmBinding(task, staffId) {
    setBindingTask(null);
    const selected = dashboard.staff.find((entry) => entry.staffId === staffId);
    setToast("演示完成：已为 " + task.staffTrack + " 选择 " + (selected?.staffName || staffId) + "。阶段 1 未写入 API。");
  }

  return (
    <div className={"app-shell app-shell--" + theme}>
      <StoreHealthBar healthServices={dashboard.healthServices} onHealthToggle={(id) => setOpenHealthId((current) => current === id ? null : id)} onThemeToggle={() => setTheme((current) => current === "light" ? "dark" : "light")} openHealthId={openHealthId} source={dashboard.sources.health} store={dashboard.store} theme={theme} />
      <main className="operations-console" id="main-content">
        {dashboard.mode === "live" && hasErrors && <section className="api-error-banner" role="alert"><div><strong>部分只读运营数据暂不可用</strong><span>页面保留已成功加载的数据；无法确认的区域不会回退为演示数据。</span></div><button onClick={refresh} type="button">重新请求</button></section>}
        {dashboard.mode === "live" && <DemoGreetingControl control={demoGreeting} />}
        <section className="top-workspace" aria-label="实时门店视图">
          <LiveCameraPanel cameraMode={cameraMode} onCameraModeChange={setCameraMode} onOverlayToggle={toggleOverlay} overlays={overlays} source={dashboard.cameraSource} />
          <CurrentCustomerPanel customer={currentCustomer} onPreviewChange={setCustomerMode} source={dashboard.currentCustomerSource} />
        </section>
        <section className="service-workspace" aria-label="服务运营工作区">
          <ActiveGreetingTable onRetry={refresh} onSelect={(task) => openDrawer("greeting", task)} source={dashboard.sources.activeGreetings} tasks={dashboard.activeGreetingTasks} />
          <RealtimeEventTimeline activeFilter={activeFilter} events={filteredEvents} onFilterChange={setActiveFilter} onRetry={refresh} onSelect={(event) => openDrawer("event", event)} source={activeFilter === "shadow" ? dashboard.sources.shadow : dashboard.sources.events} />
        </section>
        <OperationsMetricRow metrics={dashboard.metrics} onRetry={refresh} source={dashboard.sources.metrics} />
        <section className="performance-workspace" aria-label="员工服务表现">
          <StaffGreetingLeaderboard onRetry={refresh} onSelect={(staff) => openDrawer("staff", staff)} onSort={handleSort} sort={sort} source={dashboard.sources.staff} staff={sortedStaff} />
          <ServiceQualitySummary demoGreetingSummary={dashboard.demoGreetingSummary} demoSource={dashboard.sources.demoGreetings} items={dashboard.qualitySummary} legacyGreetingSummary={dashboard.legacyGreetingSummary} onRetry={refresh} source={dashboard.sources.quality} />
        </section>
        <TrendChart data={dashboard.trend} onRetry={refresh} source={dashboard.sources.trend} />
      </main>
      {drawer && <button aria-label="关闭详情" className="drawer-scrim" onClick={() => setDrawer(null)} type="button" />}
      {drawer?.kind === "staff" && <StaffGreetingDetailDrawer onClose={() => setDrawer(null)} source={dashboard.sources.staff} staff={drawer.item} />}
      {drawer?.kind === "greeting" && <EventDetailDrawer detail={drawer.item} onClose={() => setDrawer(null)} onOpenBinding={openBinding} source={dashboard.sources.activeGreetings} />}
      {drawer?.kind === "event" && <EventDetailDrawer detail={drawer.item} onClose={() => setDrawer(null)} onOpenBinding={openBinding} source={dashboard.sources.events} />}
      {bindingTask && <StaffTrackBindingDialog onClose={() => setBindingTask(null)} onConfirm={confirmBinding} staff={dashboard.staff} task={bindingTask} />}
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
