const defaultBaseUrl = import.meta.env.VITE_DEMO_GREETING_BASE_URL || "http://127.0.0.1:8082";

export class DemoGreetingApiError extends Error {
  constructor(message, { path, status, cause } = {}) {
    super(message);
    this.name = "DemoGreetingApiError";
    this.path = path;
    this.status = status;
    this.cause = cause;
  }
}

function stripTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

async function detail(response) {
  try {
    const body = await response.clone().json();
    return typeof body?.detail === "string" ? body.detail : "";
  } catch {
    return "";
  }
}

export function createDemoGreetingApi({ baseUrl = defaultBaseUrl, fetchImpl = globalThis.fetch } = {}) {
  const normalizedBaseUrl = stripTrailingSlash(baseUrl);

  async function request(path, { method = "GET", signal } = {}) {
    if (typeof fetchImpl !== "function") {
      throw new DemoGreetingApiError("Fetch API is unavailable", { path });
    }
    let response;
    try {
      response = await fetchImpl(normalizedBaseUrl + path, {
        headers: { Accept: "application/json" },
        method,
        signal,
      });
    } catch (cause) {
      if (cause?.name === "AbortError") throw cause;
      throw new DemoGreetingApiError("Unable to reach Demo greeting service", { cause, path });
    }
    if (!response.ok) {
      const message = await detail(response);
      throw new DemoGreetingApiError(
        "Demo greeting request failed (" + response.status + ")" + (message ? ": " + message : ""),
        { path, status: response.status },
      );
    }
    try {
      return await response.json();
    } catch (cause) {
      throw new DemoGreetingApiError("Demo greeting service returned invalid JSON", { cause, path, status: response.status });
    }
  }

  return {
    getStatus: ({ signal } = {}) => request("/api/demo-greeting/status", { signal }),
    reset: ({ signal } = {}) => request("/api/demo-greeting/reset", { method: "POST", signal }),
    start: ({ signal } = {}) => request("/api/demo-greeting/start", { method: "POST", signal }),
    stop: ({ signal } = {}) => request("/api/demo-greeting/stop", { method: "POST", signal }),
  };
}

export const demoGreetingApi = createDemoGreetingApi();
