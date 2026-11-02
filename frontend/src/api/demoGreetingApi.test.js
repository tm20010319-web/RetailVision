import { describe, expect, test, vi } from "vitest";
import { createDemoGreetingApi } from "./demoGreetingApi";

function response(body, status = 200) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

describe("Demo greeting API client", () => {
  test("uses the isolated service for control requests", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(response({ status: "running" }));
    const api = createDemoGreetingApi({ baseUrl: "http://localhost:8082/", fetchImpl });

    await api.start();

    expect(fetchImpl).toHaveBeenCalledWith("http://localhost:8082/api/demo-greeting/start", expect.objectContaining({ method: "POST" }));
  });

  test("preserves service errors for the operator", async () => {
    const api = createDemoGreetingApi({
      baseUrl: "http://localhost:8082",
      fetchImpl: vi.fn().mockResolvedValue(response({ detail: "worker is not running" }, 409)),
    });

    await expect(api.reset()).rejects.toEqual(expect.objectContaining({
      message: "Demo greeting request failed (409): worker is not running",
      name: "DemoGreetingApiError",
    }));
  });
});
