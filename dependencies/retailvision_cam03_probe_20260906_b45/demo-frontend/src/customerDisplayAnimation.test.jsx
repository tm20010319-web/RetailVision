import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { createWelcomeTimeline } from "./features/customer-display/animations/welcomeTimeline";
import { CustomerDisplayPage } from "./features/customer-display/CustomerDisplayPage";

vi.mock("./api", () => ({
  api: {
    getRecognitions: vi.fn(),
    getMember: vi.fn(),
  },
}));

vi.mock("./features/customer-display/animations/welcomeTimeline", () => ({
  createWelcomeTimeline: vi.fn(() => ({ kill: vi.fn() })),
}));

vi.mock("./features/customer-display/components/RobotElephantScene", () => ({
  RobotElephantScene: forwardRef(function RobotElephantScene(_props, ref) {
    return <div ref={ref} />;
  }),
}));

describe("customer display welcome animation", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.matchMedia = vi.fn(() => ({ matches: false }));
    api.getRecognitions.mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("does not restart the welcome timeline when recognition polling rerenders the page", async () => {
    render(<CustomerDisplayPage />);
    await act(async () => Promise.resolve());

    fireEvent.click(screen.getByRole("button", { name: /模拟会员到店/ }));
    expect(createWelcomeTimeline).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });

    expect(createWelcomeTimeline).toHaveBeenCalledTimes(1);
  });
});
