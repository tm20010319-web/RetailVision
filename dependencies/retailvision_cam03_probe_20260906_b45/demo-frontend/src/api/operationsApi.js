const defaultBaseUrl = import.meta.env.VITE_API_BASE_URL || "";

export class OperationsApiError extends Error {
  constructor(message, { path, status, cause } = {}) {
    super(message);
    this.name = "OperationsApiError";
    this.path = path;
    this.status = status;
    this.cause = cause;
  }
}

function stripTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

async function responseMessage(response) {
  try {
    const payload = await response.clone().json();
    return typeof payload?.detail === "string" ? payload.detail : "";
  } catch {
    return "";
  }
}

export function createOperationsApi({ baseUrl = defaultBaseUrl, fetchImpl = globalThis.fetch } = {}) {
  const normalizedBaseUrl = stripTrailingSlash(baseUrl);

  async function get(path, { signal } = {}) {
    if (typeof fetchImpl !== "function") {
      throw new OperationsApiError("Fetch API is unavailable", { path });
    }

    let response;
    try {
      response = await fetchImpl(normalizedBaseUrl + path, {
        headers: { Accept: "application/json" },
        method: "GET",
        signal,
      });
    } catch (cause) {
      if (cause?.name === "AbortError") throw cause;
      throw new OperationsApiError("Unable to reach operations API", { cause, path });
    }

    if (!response.ok) {
      const detail = await responseMessage(response);
      throw new OperationsApiError(
        "Operations API request failed (" + response.status + ")" + (detail ? ": " + detail : ""),
        { path, status: response.status },
      );
    }

    try {
      return await response.json();
    } catch (cause) {
      throw new OperationsApiError("Operations API returned invalid JSON", { cause, path, status: response.status });
    }
  }

  function pathFor(id) {
    return encodeURIComponent(id);
  }

  return {
    getBehaviorEvents: ({ signal } = {}) => get("/api/behaviors/recent?limit=50", { signal }),
    getEvidenceStatus: (clipId, { signal } = {}) => get("/api/evidence/" + pathFor(clipId), { signal }),
    getEvidenceVideoUrl: (clipId) => normalizedBaseUrl + "/api/evidence/" + pathFor(clipId) + "/video",
    getMember: (memberId, { signal } = {}) => get("/api/members/" + pathFor(memberId), { signal }),
    getMemberProfile: (memberId, { signal } = {}) => get("/api/members/" + pathFor(memberId) + "/profile", { signal }),
    getMemberVisits: (memberId, { signal } = {}) => get("/api/members/" + pathFor(memberId) + "/visits?days=30", { signal }),
    getOperationsStatus: ({ signal } = {}) => get("/api/operations/status", { signal }),
    getRecentRecognitions: ({ signal } = {}) => get("/api/recognitions/recent?limit=50", { signal }),
    getDemoStaffGreetingSummary: ({ signal } = {}) => get(
      "/api/staff-greetings/demo-summary",
      { signal },
    ),
    getStaffGreetingSummary: ({ scope = "official", signal } = {}) => get(
      "/api/staff-greetings/summary?scope=" + encodeURIComponent(scope),
      { signal },
    ),
    getGreetingV2Evaluations: ({ signal } = {}) => get("/api/operations/greetings/evaluations?limit=50", { signal }),
    getTrafficEntries: ({ signal } = {}) => get("/api/traffic/recent?limit=50", { signal }),
  };
}

export const operationsApi = createOperationsApi();
