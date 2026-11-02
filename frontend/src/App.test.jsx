import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

describe("AI 门店运营台", () => {
  test("renders the desktop operations console hierarchy", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "AI 门店运营台" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "前厅实时摄像头" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "会员焦点" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "活跃迎宾任务" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "实时 AI 事件" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "员工服务表现" })).toBeInTheDocument();
    expect(screen.getByText("今日迎宾总数")).toBeInTheDocument();
    expect(screen.getAllByText("DEMO DATA").length).toBeGreaterThan(4);
    expect(document.querySelector(".top-workspace")).toBeInTheDocument();
    expect(document.querySelector(".service-workspace")).toBeInTheDocument();
    expect(document.querySelector(".metric-grid")).toBeInTheDocument();
    expect(document.querySelector(".mobile-bottom-navigation")).not.toBeInTheDocument();
  });

  test("filters the event timeline with desktop filter controls", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "迎宾" }));

    expect(screen.getByText("Greeting Confirmed")).toBeInTheDocument();
    expect(screen.queryByText("Product Approach")).not.toBeInTheDocument();
  });

  test("opens and closes a staff detail drawer", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /张三STAFF-001/ }));

    expect(screen.getByRole("dialog", { name: "张三" })).toBeInTheDocument();
    expect(screen.getByText("按小时迎宾分布")).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "张三" })).not.toBeInTheDocument();
  });

  test("keeps an unbound employee out of personal detail attribution", () => {
    render(<App />);

    expect(screen.getByRole("button", { name: /未绑定员工仅 staff_track_token/ })).toBeDisabled();
    expect(screen.getByText("未绑定员工事件")).toBeInTheDocument();
  });

  test("opens the demo-only manual binding dialog for an unknown staff track", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "顾客 track-39" }));
    await user.click(screen.getByRole("button", { name: "人工绑定当前 Staff Track" }));

    expect(screen.getByRole("dialog", { name: "绑定当前 Staff Track" })).toBeInTheDocument();
    expect(screen.getByText("阶段 1 只演示界面，不写入数据库，也不改变员工统计。")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("绑定员工"), "STAFF-002");
    await user.click(screen.getByRole("button", { name: "确认演示绑定" }));

    expect(screen.getByRole("status")).toHaveTextContent("已为 front-runtime:track-11 选择 李敏");
    expect(screen.getByRole("status")).toHaveTextContent("阶段 1 未写入 API");
  });
});
