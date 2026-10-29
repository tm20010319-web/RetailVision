import { useCallback, useEffect, useRef, useState } from "react";
import { demoGreetingApi } from "../api/demoGreetingApi";

const initialState = {
  error: null,
  pendingAction: null,
  status: null,
  updatedAt: null,
};

function isAbort(error) {
  return error?.name === "AbortError";
}

export function useDemoGreetingControl({ api = demoGreetingApi, enabled = true, interval = 2000 } = {}) {
  const [state, setState] = useState(initialState);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async ({ signal } = {}) => {
    try {
      const status = await api.getStatus({ signal });
      if (!mountedRef.current) return null;
      setState((current) => ({ ...current, error: null, status, updatedAt: new Date().toISOString() }));
      return status;
    } catch (error) {
      if (isAbort(error) || !mountedRef.current) return null;
      setState((current) => ({ ...current, error, updatedAt: new Date().toISOString() }));
      return null;
    }
  }, [api]);

  useEffect(() => {
    if (!enabled) return undefined;
    let disposed = false;
    let controller = null;
    let timer = null;

    async function poll() {
      controller = new AbortController();
      await refresh({ signal: controller.signal });
      if (!disposed) timer = window.setTimeout(poll, interval);
    }

    poll();
    return () => {
      disposed = true;
      controller?.abort();
      if (timer) window.clearTimeout(timer);
    };
  }, [enabled, interval, refresh]);

  const action = useCallback(async (name) => {
    const method = api[name];
    if (typeof method !== "function") return null;
    setState((current) => ({ ...current, error: null, pendingAction: name }));
    try {
      const status = await method();
      if (mountedRef.current) {
        setState((current) => ({ ...current, pendingAction: null, status, updatedAt: new Date().toISOString() }));
      }
      return status;
    } catch (error) {
      if (mountedRef.current) {
        setState((current) => ({ ...current, error, pendingAction: null, updatedAt: new Date().toISOString() }));
      }
      return null;
    }
  }, [api]);

  return {
    ...state,
    refresh,
    reset: () => action("reset"),
    start: () => action("start"),
    stop: () => action("stop"),
  };
}
