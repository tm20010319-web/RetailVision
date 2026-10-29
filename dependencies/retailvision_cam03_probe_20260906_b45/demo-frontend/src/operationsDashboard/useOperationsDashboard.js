import { useCallback, useMemo } from "react";
import { operationsApi } from "../api/operationsApi";
import {
  buildDemoDashboard,
  buildLiveDashboard,
  buildUnavailableDashboard,
} from "./projection";
import { usePollingResource } from "./usePollingResource";

function resolveMode(mode) {
  if (mode) return mode;
  return import.meta.env.DEV && import.meta.env.VITE_OPERATIONS_DATA_MODE === "demo" ? "demo" : "live";
}

async function loadMemberBundle(api, memberId, signal) {
  const [member, visits, profile] = await Promise.allSettled([
    api.getMember(memberId, { signal }),
    api.getMemberVisits(memberId, { signal }),
    api.getMemberProfile(memberId, { signal }),
  ]);

  if (member.status !== "fulfilled") throw member.reason;
  return {
    member: member.value,
    profile: profile.status === "fulfilled" ? profile.value : null,
    visits: visits.status === "fulfilled" ? visits.value : null,
  };
}

async function loadEvidenceStatuses(api, clipIds, signal) {
  const results = await Promise.all(
    clipIds.map(async (clipId) => [clipId, await api.getEvidenceStatus(clipId, { signal })]),
  );
  return results.reduce((result, [clipId, value]) => {
    result[clipId] = value;
    return result;
  }, {});
}

async function loadOptionalShadowEvaluations(api, signal) {
  if (typeof api.getGreetingV2Evaluations !== "function") return null;
  try {
    return await api.getGreetingV2Evaluations({ signal });
  } catch (error) {
    // V2 is deployed independently from the V1 read models. A 404 means the
    // optional endpoint is not deployed yet, not that the whole dashboard is
    // unavailable. Other failures remain visible to the operator.
    if (error?.status === 404) return null;
    throw error;
  }
}

async function loadOptionalDemoGreetingSummary(api, signal) {
  if (typeof api.getDemoStaffGreetingSummary !== "function") return null;
  try {
    return await api.getDemoStaffGreetingSummary({ signal });
  } catch (error) {
    // Old API processes do not know this separate read model. Do not treat an
    // unscoped legacy summary as Demo data while a rolling restart is pending.
    if (error?.status === 404) return null;
    throw error;
  }
}

function latestMemberId(recognitions) {
  const items = Array.isArray(recognitions?.items) ? recognitions.items : [];
  return items
    .filter((item) => item?.member?.id)
    .sort((left, right) => Date.parse(right.occurred_at || 0) - Date.parse(left.occurred_at || 0))[0]
    ?.member?.id || null;
}

function evidenceIds(behaviors) {
  return [...new Set((Array.isArray(behaviors?.items) ? behaviors.items : [])
    .map((event) => event?.evidence?.id)
    .filter(Boolean))];
}

// Public seam for the operations read model. Callers receive one snapshot and
// one refresh command; resource polling and projection remain implementation details.
export function useOperationsDashboard({ adapter = operationsApi, mode, storeConfig } = {}) {
  const dataMode = resolveMode(mode);
  const liveEnabled = dataMode === "live";
  const getStatus = useCallback(({ signal }) => adapter.getOperationsStatus({ signal }), [adapter]);
  const getRecognitions = useCallback(({ signal }) => adapter.getRecentRecognitions({ signal }), [adapter]);
  const getTraffic = useCallback(({ signal }) => adapter.getTrafficEntries({ signal }), [adapter]);
  const getBehaviors = useCallback(({ signal }) => adapter.getBehaviorEvents({ signal }), [adapter]);
  const getStaffSummary = useCallback(
    ({ signal }) => adapter.getStaffGreetingSummary({ signal, scope: "official" }),
    [adapter],
  );
  const getDemoStaffSummary = useCallback(
    ({ signal }) => loadOptionalDemoGreetingSummary(adapter, signal),
    [adapter],
  );
  const getShadowEvaluations = useCallback(
    ({ signal }) => loadOptionalShadowEvaluations(adapter, signal),
    [adapter],
  );

  const statusResource = usePollingResource(getStatus, {
    enabled: liveEnabled,
    interval: 5000,
    resourceKey: "operations-status",
  });
  const recognitionResource = usePollingResource(getRecognitions, {
    enabled: liveEnabled,
    interval: 3000,
    resourceKey: "recent-recognitions",
  });
  const trafficResource = usePollingResource(getTraffic, {
    enabled: liveEnabled,
    interval: 3000,
    resourceKey: "recent-traffic",
  });
  const behaviorResource = usePollingResource(getBehaviors, {
    enabled: liveEnabled,
    interval: 3000,
    resourceKey: "recent-behaviors",
  });
  const staffSummaryResource = usePollingResource(getStaffSummary, {
    enabled: liveEnabled,
    interval: 5000,
    resourceKey: "staff-greeting-summary",
  });
  const demoStaffSummaryResource = usePollingResource(getDemoStaffSummary, {
    enabled: liveEnabled,
    interval: 5000,
    resourceKey: "staff-greeting-demo-summary",
  });
  const shadowResource = usePollingResource(getShadowEvaluations, {
    enabled: liveEnabled,
    interval: 3000,
    resourceKey: "greeting-v2-shadow",
  });

  const memberId = latestMemberId(recognitionResource.data);
  const getMemberBundle = useCallback(
    ({ signal }) => loadMemberBundle(adapter, memberId, signal),
    [adapter, memberId],
  );
  const memberResource = usePollingResource(getMemberBundle, {
    enabled: liveEnabled && Boolean(memberId),
    interval: 10000,
    resourceKey: "member-focus:" + (memberId || "none"),
  });

  const clipIds = useMemo(() => evidenceIds(behaviorResource.data), [behaviorResource.data]);
  const pendingEvidence = behaviorResource.data?.items?.some((event) => event?.evidence?.id && !event.evidence.available);
  const getEvidenceStatuses = useCallback(
    ({ signal }) => loadEvidenceStatuses(adapter, clipIds, signal),
    [adapter, clipIds],
  );
  const evidenceResource = usePollingResource(getEvidenceStatuses, {
    enabled: liveEnabled && clipIds.length > 0,
    interval: pendingEvidence ? 2000 : 10000,
    resourceKey: "evidence:" + clipIds.join(","),
  });

  const dashboard = useMemo(() => {
    if (dataMode === "demo") return buildDemoDashboard();
    if (dataMode !== "live") return buildUnavailableDashboard(storeConfig);
    return buildLiveDashboard({
      behaviorResource,
      evidenceResource,
      getEvidenceVideoUrl: adapter.getEvidenceVideoUrl,
      memberResource,
      recognitionResource,
      shadowResource,
      demoStaffSummaryResource,
      staffSummaryResource,
      statusResource,
      storeConfig,
      trafficResource,
    });
  }, [
    adapter.getEvidenceVideoUrl,
    behaviorResource,
    dataMode,
    evidenceResource,
    memberResource,
    recognitionResource,
    shadowResource,
    demoStaffSummaryResource,
    staffSummaryResource,
    statusResource,
    storeConfig,
    trafficResource,
  ]);

  const hasErrors = [
    statusResource,
    recognitionResource,
    trafficResource,
    behaviorResource,
    shadowResource,
    staffSummaryResource,
    demoStaffSummaryResource,
    memberResource,
    evidenceResource,
  ].some((resource) => resource.status === "error" || resource.status === "stale");
  const refresh = useCallback(() => {
    statusResource.retry();
    recognitionResource.retry();
    trafficResource.retry();
    behaviorResource.retry();
    shadowResource.retry();
    staffSummaryResource.retry();
    demoStaffSummaryResource.retry();
    memberResource.retry();
    evidenceResource.retry();
  }, [
    behaviorResource.retry,
    demoStaffSummaryResource.retry,
    evidenceResource.retry,
    memberResource.retry,
    recognitionResource.retry,
    shadowResource.retry,
    staffSummaryResource.retry,
    statusResource.retry,
    trafficResource.retry,
  ]);
  return {
    hasErrors,
    refresh,
    snapshot: dashboard,
  };
}
