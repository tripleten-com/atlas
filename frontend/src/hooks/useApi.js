import { useCallback, useEffect, useRef, useState } from "react";

// Run an API call and keep its result in state.
//
//   const { data, error, loading, reload } = useApi(() => getPatient(id), [id]);
//
// `deps` works like a useEffect dependency array: list every value the call reads.
// Pass `{ keepPrevious: true }` to keep showing old data while a refresh is in flight.
export function useApi(fn, deps = [], { enabled = true, keepPrevious = false } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(enabled);
  const callId = useRef(0);

  const run = useCallback(
    (quiet = false) => {
      if (!enabled) return Promise.resolve();
      const id = ++callId.current;
      if (!quiet) {
        setLoading(true);
        if (!keepPrevious) setData(null);
      }
      setError(null);
      return fn()
        .then((result) => {
          if (id === callId.current) setData(result);
        })
        .catch((err) => {
          if (id === callId.current && err.name !== "AbortError") setError(err);
        })
        .finally(() => {
          if (id === callId.current) setLoading(false);
        });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [enabled, ...deps],
  );

  useEffect(() => {
    run();
  }, [run]);

  // reload() shows the loading state; refresh() updates silently (for live data).
  const reload = useCallback(() => run(false), [run]);
  const refresh = useCallback(() => run(true), [run]);
  return { data, error, loading, reload, refresh, setData };
}
