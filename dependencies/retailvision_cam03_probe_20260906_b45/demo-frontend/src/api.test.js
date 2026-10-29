import { afterEach, describe, expect, it, vi } from "vitest";
import { api, resolveApiBaseUrl } from "./api";

describe("API base URL", () => {
  it("uses same-origin API paths by default", () => {
    expect(resolveApiBaseUrl()).toBe("");
  });

  it("keeps an explicitly configured API URL", () => {
    expect(resolveApiBaseUrl("http://api.example.test:9000/"))
      .toBe("http://api.example.test:9000");
  });
});

describe("api.getVisits", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests one day of visits by default", async () => {
    const signal = new AbortController().signal;
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ visits: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.getVisits("member /1", signal);

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/members\/member%20%2F1\/visits\?days=1$/),
      { signal },
    );
  });

  it("URL-encodes an explicit days value", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ visits: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.getVisits("member-1", undefined, "1/2");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/members\/member-1\/visits\?days=1%2F2$/),
      { signal: undefined },
    );
  });
});

describe("behavior APIs", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests recent behavior and the selected peak range", async () => {
    const signal = new AbortController().signal;
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ items: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.getBehaviors(25, signal);
    await api.getTrafficEntries(20, signal);
    await api.getPeak(7, signal);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      expect.stringMatching(/\/api\/behaviors\/recent\?limit=25$/),
      { signal },
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      expect.stringMatching(/\/api\/traffic\/recent\?limit=20$/),
      { signal },
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      expect.stringMatching(/\/api\/traffic\/peak\?days=7$/),
      { signal },
    );
  });

  it("posts a peak evidence request and builds an encoded video URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ evidence: { id: "clip-1" } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.requestPeakEvidence(3);

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/traffic\/peak\/evidence$/),
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ days: 3 }),
        signal: undefined,
      },
    );
    expect(api.getEvidenceVideoUrl("clip /1")).toMatch(
      /\/api\/evidence\/clip%20%2F1\/video$/,
    );
  });
});
