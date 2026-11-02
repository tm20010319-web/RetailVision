export function resolveApiBaseUrl(
  configuredUrl = import.meta.env.VITE_API_BASE_URL,
) {
  return configuredUrl ? configuredUrl.replace(/\/$/, "") : "";
}

const API_BASE_URL = resolveApiBaseUrl();

export class ApiError extends Error {
  constructor(status, message = `API request failed with status ${status}`) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function get(path, signal) {
  const response = await fetch(`${API_BASE_URL}${path}`, { signal });

  if (!response.ok) {
    throw new ApiError(response.status);
  }

  return response.json();
}

async function post(path, body, signal) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    throw new ApiError(response.status);
  }

  return response.json();
}

function memberPath(memberId, suffix = "") {
  return `/api/members/${encodeURIComponent(memberId)}${suffix}`;
}

export const api = {
  getOperations(signal) {
    return get("/api/operations/status", signal);
  },

  getRecognitions(limit = 20, signal) {
    return get(`/api/recognitions/recent?limit=${encodeURIComponent(limit)}`, signal);
  },

  getBehaviors(limit = 50, signal) {
    return get(`/api/behaviors/recent?limit=${encodeURIComponent(limit)}`, signal);
  },

  getTrafficEntries(limit = 20, signal) {
    return get(`/api/traffic/recent?limit=${encodeURIComponent(limit)}`, signal);
  },

  getPeak(days = 3, signal) {
    return get(`/api/traffic/peak?days=${encodeURIComponent(days)}`, signal);
  },

  requestPeakEvidence(days = 3, signal) {
    return post("/api/traffic/peak/evidence", { days }, signal);
  },

  getEvidenceStatus(clipId, signal) {
    return get(`/api/evidence/${encodeURIComponent(clipId)}`, signal);
  },

  getEvidenceVideoUrl(clipId) {
    return `${API_BASE_URL}/api/evidence/${encodeURIComponent(clipId)}/video`;
  },

  getMember(memberId, signal) {
    return get(memberPath(memberId), signal);
  },

  getVisits(memberId, signal, days = 1) {
    return get(memberPath(memberId, `/visits?days=${encodeURIComponent(days)}`), signal);
  },

  getProfile(memberId, signal) {
    return get(memberPath(memberId, "/profile"), signal);
  },
};
