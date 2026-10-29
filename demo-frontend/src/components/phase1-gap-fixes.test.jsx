import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ActiveGreetingTable } from "./ActiveGreetingTable";
import { EvidenceClipPlayer } from "./EvidenceClipPlayer";
import { LiveCameraPanel } from "./LiveCameraPanel";
import { RealtimeEventTimeline } from "./RealtimeEventTimeline";
import { StaffGreetingLeaderboard } from "./StaffGreetingLeaderboard";
import { StoreHealthBar } from "./StoreHealthBar";
import {
  activeGreetingTasks,
  demoEvents,
  healthServices,
  staffGreetingStats,
  storeContext,
} from "../data/operationsDemo";

function CameraHarness() {
  const [overlays, setOverlays] = useState({ boxes: true, zones: true, paths: true, debug: false });
  return (
    <LiveCameraPanel
      cameraMode="live"
      onCameraModeChange={() => undefined}
      onOverlayToggle={(key) => setOverlays((current) => ({ ...current, [key]: !current[key] }))}
      overlays={overlays}
    />
  );
}

describe("阶段 1 桌面运营台补充交互", () => {
  test("shows text status on compact health chips", () => {
    render(
      <StoreHealthBar
        healthServices={healthServices}
        onHealthToggle={() => undefined}
        onThemeToggle={() => undefined}
        openHealthId={null}
        store={storeContext}
        theme="light"
      />,
    );

    expect(screen.getByRole("button", { name: /Poll警告/ })).toBeInTheDocument();
  });

  test("toggles world-coordinate overlay independently", async () => {
    const user = userEvent.setup();
    render(<CameraHarness />);

    expect(screen.queryByText("foot · x 2.41 · y 4.18")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "世界坐标" }));
    expect(screen.getByText("foot · x 2.41 · y 4.18")).toBeInTheDocument();
  });

  test("renders staff track and compact visit ID in desktop lists", () => {
    render(<ActiveGreetingTable onSelect={() => undefined} tasks={activeGreetingTasks} />);
    expect(screen.getByTitle("front-runtime:track-7")).toHaveTextContent("front-r...ck-7");

    render(
      <RealtimeEventTimeline
        activeFilter="all"
        events={demoEvents}
        onFilterChange={() => undefined}
        onSelect={() => undefined}
      />,
    );
    expect(screen.getAllByText("V VST-9A31").length).toBeGreaterThan(0);
  });

  test("filters the mock leaderboard by staff", async () => {
    const user = userEvent.setup();
    render(
      <StaffGreetingLeaderboard
        onSelect={() => undefined}
        onSort={() => undefined}
        sort={{ key: "greetingCount", direction: "desc" }}
        staff={staffGreetingStats}
      />,
    );

    await user.selectOptions(screen.getByLabelText("员工"), "STAFF-002");
    expect(screen.getByRole("button", { name: /李敏STAFF-002/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /张三STAFF-001/ })).not.toBeInTheDocument();
  });

  test("gives evidence preview button an observable demo response", async () => {
    const user = userEvent.setup();
    render(<EvidenceClipPlayer evidence="已就绪" label="greeting-078" />);

    await user.click(screen.getByRole("button", { name: "查看 greeting-078 的演示证据" }));
    expect(screen.getByRole("status")).toHaveTextContent("演示证据预览已打开");
  });

  test("does not make pending demo evidence playable", () => {
    render(<EvidenceClipPlayer evidence="待生成" label="greeting-081" />);

    expect(screen.getByRole("button", { name: "证据不可用" })).toBeDisabled();
    expect(screen.getByText("证据视频尚不可用")).toBeInTheDocument();
  });
});
