import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import App from "./App";

function emptyApi() {
  return {
    getBehaviorEvents: async () => ({ items: [] }),
    getEvidenceStatus: async () => ({}),
    getEvidenceVideoUrl: (id) => "/api/evidence/" + id + "/video",
    getMember: async () => ({}),
    getMemberProfile: async () => ({}),
    getMemberVisits: async () => ({ visits: [] }),
    getOperationsStatus: async () => ({ api: { state: "online" }, generated_at: "2026-07-30T10:00:00" }),
    getRecentRecognitions: async () => ({ items: [] }),
    getStaffGreetingSummary: async () => ({ items: [] }),
    getTrafficEntries: async () => ({ items: [] }),
  };
}

function failingApi() {
  const fail = async () => { throw new Error("offline"); };
  return {
    getBehaviorEvents: fail,
    getEvidenceStatus: fail,
    getEvidenceVideoUrl: () => "/api/evidence/clip/video",
    getMember: fail,
    getMemberProfile: fail,
    getMemberVisits: fail,
    getOperationsStatus: fail,
    getRecentRecognitions: fail,
    getStaffGreetingSummary: fail,
    getTrafficEntries: fail,
  };
}

describe("live dashboard data states", () => {
  test("shows explicit unavailable states for missing read models instead of demo content", async () => {
    render(<App apiClient={emptyApi()} dataMode="live" />);

    await screen.findByText("暂无可用会员焦点");
    expect(screen.getByText("活跃迎宾任务不可用")).toBeInTheDocument();
    expect(screen.getByText("员工迎宾排行不可用")).toBeInTheDocument();
    expect(screen.queryByText("DEMO DATA")).not.toBeInTheDocument();
  });

  test("keeps a visible recovery path when read-only API requests fail", async () => {
    render(<App apiClient={failingApi()} dataMode="live" />);

    await waitFor(() => expect(screen.getByText("部分只读运营数据暂不可用")).toBeInTheDocument());
    expect(screen.getAllByText(/运营 API 请求失败/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "重新请求" })).toBeInTheDocument();
  });

  test("keeps the V1 dashboard usable when the optional V2 endpoint is not deployed", async () => {
    const api = emptyApi();
    api.getGreetingV2Evaluations = async () => {
      const error = new Error("not found");
      error.status = 404;
      throw error;
    };

    render(<App apiClient={api} dataMode="live" />);

    await screen.findByText("暂无可用会员焦点");
    expect(screen.queryByText("部分只读运营数据暂不可用")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "V2 Shadow" })).toBeInTheDocument();
  });
});
