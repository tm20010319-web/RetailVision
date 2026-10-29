import { useCallback, useEffect, useRef, useState } from "react";

const initialState = {
  data: null,
  error: null,
  key: null,
  status: "disabled",
  updatedAt: null,
};

function isAbort(error) {
  return error?.name === "AbortError";
}

export function usePollingResource(load, {
  enabled = true,
  initialData = null,
  interval = 0,
  resourceKey = "default",
} = {}) {
  const loadRef = useRef(load);
  const [requestVersion, setRequestVersion] = useState(0);
  const [state, setState] = useState(initialState);

  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  useEffect(() => {
    if (!enabled) {
      setState((previous) => ({
        data: previous.key === resourceKey ? previous.data : initialData,
        error: null,
        key: resourceKey,
        status: "disabled",
        updatedAt: previous.key === resourceKey ? previous.updatedAt : null,
      }));
      return undefined;
    }

    let disposed = false;
    let timer = null;
    let controller = null;

    async function run() {
      controller = new AbortController();
      setState((previous) => {
        const retainedData = previous.key === resourceKey ? previous.data : initialData;
        return {
          data: retainedData,
          error: null,
          key: resourceKey,
          status: retainedData === null || retainedData === undefined ? "loading" : "refreshing",
          updatedAt: previous.key === resourceKey ? previous.updatedAt : null,
        };
      });

      try {
        const data = await loadRef.current({ signal: controller.signal });
        if (disposed) return;
        setState({
          data,
          error: null,
          key: resourceKey,
          status: "success",
          updatedAt: new Date().toISOString(),
        });
      } catch (error) {
        if (disposed || isAbort(error)) return;
        setState((previous) => ({
          data: previous.key === resourceKey ? previous.data : initialData,
          error,
          key: resourceKey,
          status: previous.key === resourceKey && previous.data != null ? "stale" : "error",
          updatedAt: previous.key === resourceKey ? previous.updatedAt : null,
        }));
      } finally {
        if (!disposed && interval > 0) timer = window.setTimeout(run, interval);
      }
    }

    run();
    return () => {
      disposed = true;
      controller?.abort();
      if (timer) window.clearTimeout(timer);
    };
  }, [enabled, initialData, interval, requestVersion, resourceKey]);

  const retry = useCallback(() => setRequestVersion((value) => value + 1), []);
  return { ...state, retry };
}
