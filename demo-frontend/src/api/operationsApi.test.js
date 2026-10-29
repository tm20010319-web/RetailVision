import { describe, expect, test, vi } from "vitest";
import { OperationsApiError, createOperationsApi } from "./operationsApi";

describe("operations API client", () => {
  test("uses only GET requests and encodes evidence identifiers", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "clip/a" }), { status: 200 }));
    const api = createOperationsApi({ baseUrl: "http://localhost:8081/", fetchImpl });

    await expect(api.getEvidenceStatus("clip/a")).resolves.toEqual({ id: "clip/a" });
    expect(fetchImpl).toHaveBeenCalledWith("http://localhost:8081/api/evidence/clip%2Fa", expect.objectContaining({ method: "GET" }));
    expect(api.getEvidenceVideoUrl("clip/a")).toBe("http://localhost:8081/api/evidence/clip%2Fa/video");
  });

  test("normalizes network failures without leaking them into the UI contract", async () => {
    const api = createOperationsApi({
      fetchImpl: vi.fn().mockRejectedValue(new Error("connection refused")),
    });

    await expect(api.getOperationsStatus()).rejects.toBeInstanceOf(OperationsApiError);
  });

  test("requests the explicitly isolated demo greeting summary", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [] }), { status: 200 }));
    const api = createOperationsApi({ baseUrl: "http://localhost:8081", fetchImpl });

    await api.getDemoStaffGreetingSummary();

    expect(fetchImpl).toHaveBeenCalledWith(
      "http://localhost:8081/api/staff-greetings/demo-summary",
      expect.objectContaining({ method: "GET" }),
    );
  });
});
