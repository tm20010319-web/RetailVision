import { describe, expect, it, vi } from "vitest";
import {
  ARRIVAL_COOLDOWN_MS,
  ARRIVAL_QUEUE_LIMIT,
  ARRIVAL_QUEUE_TTL_MS,
  MemberArrivalController,
} from "./features/customer-display/memberArrivalController";

const event = (id, memberId = id, overrides = {}) => ({
  event_id: id,
  status: "processed",
  member: { id: memberId },
  visit_id: `visit-${id}`,
  occurred_at: `2026-08-01T00:00:0${id}Z`,
  ...overrides,
});

function createController(options = {}) {
  let now = 1_000_000;
  const onChange = vi.fn();
  const controller = new MemberArrivalController({
    getRecognitions: vi.fn(),
    getMember: vi.fn(async (id) => ({ id, name: id, display_name: id })),
    onChange,
    now: () => now,
    ...options,
  });
  return { controller, onChange, advance: (ms) => { now += ms; } };
}

describe("MemberArrivalController", () => {
  it("does not add a second frontend cooldown on top of backend arrival deduplication", () => {
    expect(ARRIVAL_COOLDOWN_MS).toBe(0);
  });

  it("establishes a baseline without replaying historical events", async () => {
    const { controller } = createController();
    controller.handlePoll({ items: [event("old")] });
    expect(controller.current).toBeNull();
    controller.handlePoll({ items: [event("new"), event("old")] });
    await vi.waitFor(() => expect(controller.current?.member.id).toBe("new"));
  });

  it("filters out non-processed, incomplete, and visit-less recognitions", async () => {
    const getMember = vi.fn();
    const { controller } = createController({ getMember });
    controller.handlePoll({ items: [] });
    controller.handlePoll({
      items: [
        event("pending", "pending", { status: "processing" }),
        event("anonymous", "anonymous", { member: null }),
        event("no-visit", "no-visit", { visit_id: null }),
      ],
    });
    await Promise.resolve();
    expect(getMember).not.toHaveBeenCalled();
    expect(controller.current).toBeNull();
  });

  it("reconsiders a new event after it transitions from received to processed", async () => {
    const { controller } = createController();
    controller.handlePoll({ items: [] });
    controller.handlePoll({
      items: [event("e1", "m1", { status: "received", visit_id: null })],
    });
    await Promise.resolve();
    expect(controller.current).toBeNull();

    controller.handlePoll({ items: [event("e1", "m1")] });

    await vi.waitFor(() => expect(controller.current?.member.id).toBe("m1"));
  });

  it("retries member detail twice before abandoning the request", async () => {
    const getMember = vi
      .fn()
      .mockRejectedValueOnce(new Error("temporary"))
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValue({ id: "m1", name: "李", display_name: "李女士" });
    const { controller } = createController({ getMember });
    controller.handlePoll({ items: [] });
    controller.handlePoll({ items: [event("e1", "m1")] });
    await vi.waitFor(() => expect(controller.current?.member.id).toBe("m1"));
    expect(getMember).toHaveBeenCalledTimes(3);
  });

  it("retries an event on a later poll when member details were temporarily unavailable", async () => {
    const getMember = vi
      .fn()
      .mockRejectedValueOnce(new Error("temporary"))
      .mockRejectedValueOnce(new Error("temporary"))
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValue({ id: "m1", name: "李", display_name: "李女士" });
    const { controller } = createController({ getMember });
    controller.handlePoll({ items: [] });
    controller.handlePoll({ items: [event("e1", "m1")] });
    await vi.waitFor(() => expect(getMember).toHaveBeenCalledTimes(3));
    expect(controller.current).toBeNull();

    controller.handlePoll({ items: [event("e1", "m1")] });

    await vi.waitFor(() => expect(controller.current?.member.id).toBe("m1"));
    expect(getMember).toHaveBeenCalledTimes(4);
  });

  it("can welcome the same member again after the previous animation completes", async () => {
    const { controller } = createController();
    controller.handlePoll({ items: [] });
    controller.handlePoll({ items: [event("e1", "m1")] });
    await vi.waitFor(() => expect(controller.current?.eventId).toBe("e1"));
    controller.complete();

    controller.handlePoll({ items: [event("e2", "m1"), event("e1", "m1")] });

    await vi.waitFor(() => expect(controller.current?.eventId).toBe("e2"));
  });

  it("keeps FIFO order and caps the queue at three requests", () => {
    const { controller } = createController();
    controller.enqueue({ member: { id: "a" }, createdAt: 1_000_000 });
    ["b", "c", "d", "e"].forEach((id) => controller.enqueue({ member: { id }, createdAt: 1_000_000 }));
    expect(controller.current.member.id).toBe("a");
    expect(controller.queue).toHaveLength(ARRIVAL_QUEUE_LIMIT);
    controller.complete();
    expect(controller.current.member.id).toBe("b");
  });

  it("preserves event order when later member details resolve first", async () => {
    let resolveFirst;
    const getMember = vi.fn((id) => id === "a"
      ? new Promise((resolve) => { resolveFirst = () => resolve({ id }); })
      : Promise.resolve({ id }));
    const { controller } = createController({ getMember });
    controller.handlePoll({ items: [] });
    controller.handlePoll({ items: [event("a", "a"), event("b", "b")] });
    await vi.waitFor(() => expect(getMember).toHaveBeenCalledTimes(1));
    expect(controller.current).toBeNull();
    resolveFirst();
    await vi.waitFor(() => expect(controller.current?.member.id).toBe("a"));
    expect(controller.queue[0]?.member.id).toBe("b");
  });

  it("drops queued requests older than thirty seconds", () => {
    const { controller, advance } = createController();
    controller.enqueue({ member: { id: "a" }, createdAt: 1_000_000 });
    controller.enqueue({ member: { id: "b" }, createdAt: 1_000_000 });
    advance(ARRIVAL_QUEUE_TTL_MS + 1);
    controller.complete();
    expect(controller.current).toBeNull();
  });
});
