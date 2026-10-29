import { useCallback, useEffect, useRef, useState } from "react";

export const ARRIVAL_POLL_INTERVAL_MS = 1000;
export const ARRIVAL_COOLDOWN_MS = 0;
export const ARRIVAL_QUEUE_LIMIT = 3;
export const ARRIVAL_QUEUE_TTL_MS = 30_000;
export const MEMBER_DETAIL_ATTEMPTS = 3;

const TERMINAL_NON_ARRIVAL_STATUSES = new Set(["ignored", "unmapped"]);

export function isValidArrivalEvent(item) {
  return Boolean(
    item &&
      item.status === "processed" &&
      item.member &&
      item.member.id &&
      item.visit_id,
  );
}

export async function fetchMemberWithRetry(
  getMember,
  memberId,
  { attempts = MEMBER_DETAIL_ATTEMPTS, signal } = {},
) {
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await getMember(memberId, signal);
    } catch (error) {
      lastError = error;
      if (signal?.aborted) throw error;
    }
  }
  throw lastError || new Error("member detail unavailable");
}

export class MemberArrivalController {
  constructor({
    getRecognitions,
    getMember,
    onChange,
    now = () => Date.now(),
    pollIntervalMs = ARRIVAL_POLL_INTERVAL_MS,
    cooldownMs = ARRIVAL_COOLDOWN_MS,
    queueLimit = ARRIVAL_QUEUE_LIMIT,
    queueTtlMs = ARRIVAL_QUEUE_TTL_MS,
  }) {
    this.getRecognitions = getRecognitions;
    this.getMember = getMember;
    this.onChange = onChange;
    this.now = now;
    this.pollIntervalMs = pollIntervalMs;
    this.cooldownMs = cooldownMs;
    this.queueLimit = queueLimit;
    this.queueTtlMs = queueTtlMs;
    this.seenEventIds = new Set();
    this.pendingEventIds = new Set();
    this.lastShownAt = new Map();
    this.pendingMemberIds = new Set();
    this.queue = [];
    this.current = null;
    this.baselined = false;
    this.timer = null;
    this.abortController = null;
    this.pollInFlight = false;
    this.resolutionChain = Promise.resolve();
  }

  start() {
    if (this.timer) return;
    this.abortController = new AbortController();
    this.poll();
    this.timer = setInterval(() => this.poll(), this.pollIntervalMs);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.abortController?.abort();
    this.abortController = null;
    this.queue = [];
    this.pendingEventIds.clear();
    this.pendingMemberIds.clear();
  }

  async poll() {
    if (this.pollInFlight || !this.abortController) return;
    this.pollInFlight = true;
    try {
      const result = await this.getRecognitions(20, this.abortController.signal);
      this.handlePoll(result);
    } catch {
      if (!this.abortController?.signal.aborted) this.onChange?.(this.snapshot());
    } finally {
      this.pollInFlight = false;
    }
  }

  handlePoll(result) {
    const items = Array.isArray(result?.items) ? result.items : [];
    if (!this.baselined) {
      items.forEach((item) => item?.event_id && this.seenEventIds.add(item.event_id));
      this.baselined = true;
      this.onChange?.(this.snapshot());
      return;
    }

    const fresh = items
      .filter((item) => (
        item?.event_id
        && !this.seenEventIds.has(item.event_id)
        && !this.pendingEventIds.has(item.event_id)
      ))
      .sort((left, right) => String(left.occurred_at || "").localeCompare(String(right.occurred_at || "")));
    const validFresh = fresh.filter(isValidArrivalEvent);
    fresh.forEach((item) => {
      if (TERMINAL_NON_ARRIVAL_STATUSES.has(item?.status)) {
        this.seenEventIds.add(item.event_id);
      }
    });
    if (validFresh.length) {
      validFresh.forEach((item) => this.pendingEventIds.add(item.event_id));
      this.resolutionChain = this.resolutionChain.then(() =>
        validFresh.reduce(
          (chain, item) => chain.then(() => this.resolveArrival(item)),
          Promise.resolve(),
        ),
      );
    }
    this.dropExpiredQueue();
    this.onChange?.(this.snapshot());
  }

  async resolveArrival(item) {
    const memberId = item.member.id;
    const lastShown = this.lastShownAt.get(memberId) || 0;
    if (this.now() - lastShown < this.cooldownMs || this.pendingMemberIds.has(memberId)) {
      this.seenEventIds.add(item.event_id);
      this.pendingEventIds.delete(item.event_id);
      return;
    }
    this.pendingMemberIds.add(memberId);
    try {
      const member = await fetchMemberWithRetry(this.getMember, memberId, {
        signal: this.abortController?.signal,
      });
      if (
        !member ||
        !this.pendingMemberIds.has(memberId) ||
        this.abortController?.signal.aborted
      ) return;
      this.enqueue({ member, eventId: item.event_id, createdAt: this.now() });
      this.seenEventIds.add(item.event_id);
    } catch {
      if (!this.abortController?.signal.aborted) this.onChange?.(this.snapshot());
    } finally {
      this.pendingEventIds.delete(item.event_id);
      this.pendingMemberIds.delete(memberId);
    }
  }

  enqueue(request) {
    if (this.current?.member?.id === request.member.id || this.queue.some((entry) => entry.member?.id === request.member.id)) {
      return;
    }
    if (this.current) {
      this.dropExpiredQueue();
      if (this.queue.length >= this.queueLimit) return;
      this.queue.push(request);
    } else {
      this.current = request;
      this.lastShownAt.set(request.member.id, this.now());
    }
    this.emit();
  }

  enqueueMock(member) {
    this.enqueue({ member, eventId: `mock-${this.now()}`, createdAt: this.now(), mock: true });
  }

  complete() {
    this.current = null;
    this.dropExpiredQueue();
    const next = this.queue.shift();
    if (next) {
      this.current = next;
      this.lastShownAt.set(next.member.id, this.now());
    }
    this.emit();
  }

  dropExpiredQueue() {
    const cutoff = this.now() - this.queueTtlMs;
    this.queue = this.queue.filter((entry) => entry.createdAt >= cutoff);
  }

  snapshot() {
    this.dropExpiredQueue();
    return { current: this.current, queueLength: this.queue.length };
  }

  emit() {
    this.onChange?.(this.snapshot());
  }
}

export function useMemberArrivalController({ getRecognitions, getMember, mockData }) {
  const [, forceUpdate] = useState(0);
  const controllerRef = useRef(null);
  if (!controllerRef.current) {
    controllerRef.current = new MemberArrivalController({
      getRecognitions,
      getMember,
      onChange: () => forceUpdate((value) => value + 1),
    });
  }

  useEffect(() => {
    const controller = controllerRef.current;
    controller.start();
    return () => controller.stop();
  }, []);

  const complete = useCallback(() => controllerRef.current.complete(), []);
  const triggerMock = useCallback(() => controllerRef.current.enqueueMock(mockData), [mockData]);
  const snapshot = controllerRef.current.snapshot();
  return {
    currentMember: snapshot.current?.member || null,
    queueLength: snapshot.queueLength,
    complete,
    triggerMock,
  };
}
