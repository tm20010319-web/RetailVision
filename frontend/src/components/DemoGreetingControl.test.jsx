import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { DemoGreetingControl } from "./DemoGreetingControl";

function control(overrides = {}) {
  return {
    error: null,
    pendingAction: null,
    reset: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    status: {
      last_event: null,
      person_count: 2,
      session_event_count: 0,
      source: "rtsp://admin:***@192.0.2.2:554/live/0/MAIN",
      staff_count: 1,
      status: "running",
      running: true,
    },
    ...overrides,
  };
}

describe("DemoGreetingControl", () => {
  test("shows an explicit real-camera Demo state and routes commands to the worker", () => {
    const value = control();
    render(<DemoGreetingControl control={value} />);

    expect(screen.getByText("DEMO ONLY")).toBeInTheDocument();
    expect(screen.getByText("实时检测中")).toBeInTheDocument();
    expect(screen.queryByText("员工身份未绑定，证据待生成。", { exact: false })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "新一轮 Demo" }));
    fireEvent.click(screen.getByRole("button", { name: "重置" }));
    fireEvent.click(screen.getByRole("button", { name: "停止" }));

    expect(value.start).toHaveBeenCalledOnce();
    expect(value.reset).toHaveBeenCalledOnce();
    expect(value.stop).toHaveBeenCalledOnce();
  });
});
