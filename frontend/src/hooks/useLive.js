import { useCallback, useEffect, useRef, useState } from "react";
/** Subscribe to the server's SSE stream; `onEvent` is called for every event. Reconnects automatically. */
export function useLiveEvents(onEvent, enabled = true) {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") return;
    const source = new EventSource("/v1/events", { withCredentials: true });
    source.onmessage = (message) => {
      try {
        handler.current(JSON.parse(message.data));
      } catch {
        /* ignore malformed keepalives */
      }
    };
    return () => source.close();
  }, [enabled]);
}
/** Load data, refresh on demand, and poll as a fallback when live events are unavailable. */
export function useResource(loader, deps, pollMs = 0) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const reload = useCallback(async () => {
    try {
      const value = await loaderRef.current();
      setData(value);
      setError(null);
      return value;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    setLoading(true);
    void reload();
    if (!pollMs) return;
    const timer = window.setInterval(() => void reload(), pollMs);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, setData, error, loading, reload };
}
