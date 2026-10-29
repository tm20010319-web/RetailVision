import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { useOperationsDashboard } from ".";

function createAdapter(overrides = {}) {
  return {
    getBehaviorEvents: vi.fn(async () => ({ items: [] })),
    getDemoStaffGreetingSummary: vi.fn(async () => ({ items: [] })),
    getEvidenceStatus: vi.fn(async (id) => ({ available: true, id, status: "ready" })),
    getEvidenceVideoUrl: (id) => "/api/evidence/" + id + "/video",
    getGreetingV2Evaluations: vi.fn(async () => ({ items: [] })),
    getMember: vi.fn(async (id) => ({ id, name: "测试会员", tier: "gold" })),
    getMemberProfile: vi.fn(async () => ({ profile: { style_tags: ["简约"] }, status: "completed" })),
    getMemberVisits: vi.fn(async () => ({ visits: [] })),
    getOperationsStatus: vi.fn(async () => ({
      api: { state: "online" },
      generated_at: "2026-08-03T10:00:00",
    })),
    getRecentRecognitions: vi.fn(async () => ({ items: [] })),
    getStaffGreetingSummary: vi.fn(async () => ({ items: [] })),
    getTrafficEntries: vi.fn(async () => ({ items: [] })),
    ...overrides,
  };
}

function notFound() {
  const error = new Error("not found");
  error.status = 404;
  return error;
}

describe("Operations Dashboard Read Model interface", () => {
  test("returns explicit Demo and unavailable snapshots without reading live data", () => {
    const demoAdapter = createAdapter();
    const demo = renderHook(() => useOperationsDashboard({ adapter: demoAdapter, mode: "demo" }));

    expect(demo.result.current.snapshot.mode).toBe("demo");
    expect(demo.result.current.snapshot.sources.events.kind).toBe("demo");
    expect(demoAdapter.getOperationsStatus).not.toHaveBeenCalled();
    demo.unmount();

    const unavailableAdapter = createAdapter();
    const unavailable = renderHook(() => useOperationsDashboard({
      adapter: unavailableAdapter,
      mode: "unavailable",
    }));

    expect(unavailable.result.current.snapshot.mode).toBe("unavailable");
    expect(unavailable.result.current.snapshot.sources.events.kind).toBe("unavailable");
    expect(unavailableAdapter.getOperationsStatus).not.toHaveBeenCalled();
    unavailable.unmount();
  });

  test("projects Member, official event, Shadow, and Evidence data behind one snapshot", async () => {
    const adapter = createAdapter({
      getBehaviorEvents: vi.fn(async () => ({
        items: [{
          assessment: "confirmed",
          camera_id: "entrance-1",
          evidence: { available: false, id: "clip-1", status: "pending" },
          event_type: "staff_greeting",
          id: "behavior-1",
          started_at: "2026-08-03T10:00:02",
        }],
      })),
      getGreetingV2Evaluations: vi.fn(async () => ({
        items: [{
          cycle: { camera_id: "entrance-1", customer_track_token: "track-9" },
          evaluation: { decision: "uncertain", settled_at: "2026-08-03T10:00:03" },
          id: "shadow-1",
        }],
      })),
      getRecentRecognitions: vi.fn(async () => ({
        items: [{
          member: { id: "member-1", name: "摘要姓名", tier: "gold" },
          occurred_at: "2026-08-03T10:00:01",
          source: "push",
          status: "processed",
        }],
      })),
    });
    const { result } = renderHook(() => useOperationsDashboard({ adapter, mode: "live" }));

    await waitFor(() => expect(result.current.snapshot.currentCustomer?.id).toBe("member-1"));
    await waitFor(() => expect(adapter.getEvidenceStatus).toHaveBeenCalledWith(
      "clip-1",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));

    expect(result.current.snapshot.currentCustomer.name).toBe("测试会员");
    expect(result.current.snapshot.events.some((event) => event.id === "behavior:behavior-1")).toBe(true);
    expect(result.current.snapshot.events.some((event) => event.shadow)).toBe(false);
    expect(result.current.snapshot.events[0].id).toBe("behavior:behavior-1");
    expect(result.current.snapshot.shadowEvents).toEqual([
      expect.objectContaining({ id: "shadow:shadow-1", shadow: true }),
    ]);
    expect(result.current.hasErrors).toBe(false);
  });

  test("preserves anonymous, Demo, and configured attribution semantics in the snapshot", async () => {
    const adapter = createAdapter({
      getBehaviorEvents: vi.fn(async () => ({
        items: [
          {
            assessment: "confirmed",
            camera_id: "entrance-1",
            details: { minimum_distance_metres: 0.9, response_seconds: 5.2 },
            event_type: "staff_greeting",
            id: "configured-greeting",
            staff_attribution_source: "configured_mock",
            staff_name: "演示店员",
            started_at: "2026-08-03T10:00:02",
          },
          {
            assessment: "confirmed",
            camera_id: "entrance-1",
            details: {
              calibration_status: "provisional",
              demo_response_seconds: 0.521,
              distance_source: "ground_homography",
              minimum_distance_metres: 0.4,
              staff_travel_metres: 0.6,
            },
            event_type: "staff_greeting",
            id: "demo-greeting",
            is_demo: true,
            started_at: "2026-08-03T10:00:03",
          },
        ],
      })),
      getDemoStaffGreetingSummary: vi.fn(async () => ({
        items: [{ greeting_count: 2, staff_name: "演示店员" }],
      })),
      getRecentRecognitions: vi.fn(async () => ({
        items: [{
          occurred_at: "2026-08-03T10:00:01",
          source: "push",
          status: "waiting_mapping",
          track_id: 27,
        }],
      })),
      getStaffGreetingSummary: vi.fn(async () => ({
        items: [{ greeting_count: 3, staff_name: "演示店员" }],
      })),
    });
    const { result } = renderHook(() => useOperationsDashboard({ adapter, mode: "live" }));

    await waitFor(() => expect(result.current.snapshot.events).toHaveLength(3));
    await waitFor(() => expect(result.current.snapshot.legacyGreetingSummary?.total).toBe(3));

    const configured = result.current.snapshot.events.find((event) => event.id === "behavior:configured-greeting");
    const demo = result.current.snapshot.events.find((event) => event.id === "behavior:demo-greeting");
    expect(result.current.snapshot.currentCustomer).toMatchObject({
      kind: "anonymous",
      state: "等待会员映射",
    });
    expect(result.current.snapshot.currentCustomer.track).toContain("非身份标识");
    expect(configured.subject).toBe("演示归属：演示店员");
    expect(configured.facts.find((fact) => fact.label === "迎宾归属").value).toContain("非 Staff Identity");
    expect(demo.demo).toBe(true);
    expect(demo.facts.find((fact) => fact.label === "Demo 响应耗时").value).toContain("0.5 s");
    expect(demo.facts.find((fact) => fact.label === "距离标定").value).toBe("临时标定 · 地面单应矩阵");
    expect(result.current.snapshot.demoGreetingSummary.total).toBe(2);
    expect(result.current.snapshot.staff).toEqual([]);
    expect(result.current.snapshot.sources.quality.kind).toBe("mock_attribution");
    expect(result.current.snapshot.metrics.every((metric) => metric.value === "—")).toBe(true);
  });

  test("keeps optional 404s and optional Member bundle failures out of aggregate errors", async () => {
    const adapter = createAdapter({
      getDemoStaffGreetingSummary: vi.fn(async () => { throw notFound(); }),
      getGreetingV2Evaluations: vi.fn(async () => { throw notFound(); }),
      getMemberProfile: vi.fn(async () => { throw new Error("profile unavailable"); }),
      getMemberVisits: vi.fn(async () => { throw new Error("visits unavailable"); }),
      getRecentRecognitions: vi.fn(async () => ({
        items: [{
          member: { id: "member-2", name: "摘要姓名" },
          occurred_at: "2026-08-03T10:00:01",
          status: "processed",
        }],
      })),
    });
    const { result } = renderHook(() => useOperationsDashboard({ adapter, mode: "live" }));

    await waitFor(() => expect(result.current.snapshot.currentCustomer?.id).toBe("member-2"));
    await waitFor(() => expect(adapter.getDemoStaffGreetingSummary).toHaveBeenCalled());

    expect(result.current.snapshot.currentCustomer.name).toBe("测试会员");
    expect(result.current.snapshot.shadowEvents).toEqual([]);
    expect(result.current.hasErrors).toBe(false);
  });

  test("does not poll Evidence status when no event references a clip", async () => {
    const adapter = createAdapter();
    const { result } = renderHook(() => useOperationsDashboard({ adapter, mode: "live" }));

    await waitFor(() => expect(result.current.snapshot.sources.events.kind).toBe("live"));
    expect(adapter.getEvidenceStatus).not.toHaveBeenCalled();
  });

  test("surfaces Evidence status failures through the aggregate error state", async () => {
    const adapter = createAdapter({
      getBehaviorEvents: vi.fn(async () => ({
        items: [{ evidence: { available: false, id: "clip-failed" }, id: "behavior-1" }],
      })),
      getEvidenceStatus: vi.fn(async () => { throw new Error("evidence unavailable"); }),
    });
    const { result } = renderHook(() => useOperationsDashboard({ adapter, mode: "live" }));

    await waitFor(() => expect(adapter.getEvidenceStatus).toHaveBeenCalled());
    await waitFor(() => expect(result.current.hasErrors).toBe(true));
    expect(result.current.snapshot.mode).toBe("live");
  });

  test("retains the last successful snapshot as stale and refreshes all enabled data", async () => {
    const getOperationsStatus = vi
      .fn()
      .mockResolvedValueOnce({ api: { state: "online" }, generated_at: "2026-08-03T10:00:00" })
      .mockRejectedValue(new Error("offline"));
    const adapter = createAdapter({ getOperationsStatus });
    const { result } = renderHook(() => useOperationsDashboard({ adapter, mode: "live" }));

    await waitFor(() => expect(result.current.snapshot.sources.health.kind).toBe("live"));
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.hasErrors).toBe(true));

    expect(getOperationsStatus).toHaveBeenCalledTimes(2);
    expect(result.current.snapshot.sources.health).toMatchObject({ kind: "live", stale: true });
    expect(result.current.snapshot.store.timeIso).toBe("2026-08-03T10:00:00");
    expect(adapter.getRecentRecognitions).toHaveBeenCalledTimes(2);
  });

  test("surfaces full live failures without substituting Demo data", async () => {
    const fail = vi.fn(async () => { throw new Error("offline"); });
    const adapter = createAdapter({
      getBehaviorEvents: fail,
      getDemoStaffGreetingSummary: fail,
      getGreetingV2Evaluations: fail,
      getOperationsStatus: fail,
      getRecentRecognitions: fail,
      getStaffGreetingSummary: fail,
      getTrafficEntries: fail,
    });
    const { result } = renderHook(() => useOperationsDashboard({ adapter, mode: "live" }));

    await waitFor(() => expect(result.current.hasErrors).toBe(true));
    expect(result.current.snapshot.mode).toBe("live");
    expect(result.current.snapshot.sources.events.kind).toBe("error");
    expect(result.current.snapshot.currentCustomer).toBeNull();
  });

  test("aborts in-flight requests when the reader is disposed", async () => {
    let aborted = false;
    const getOperationsStatus = vi.fn(({ signal }) => new Promise((resolve, reject) => {
      signal.addEventListener("abort", () => {
        aborted = true;
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      });
    }));
    const adapter = createAdapter({ getOperationsStatus });
    const reader = renderHook(() => useOperationsDashboard({ adapter, mode: "live" }));

    await waitFor(() => expect(getOperationsStatus).toHaveBeenCalled());
    reader.unmount();

    expect(aborted).toBe(true);
  });
});
