import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { streamActivity } from "../api/client";
import { useAuth } from "./AuthContext";

const LiveContext = createContext(null);

// One Server-Sent Events connection for the whole app. Each time the ingestion worker applies
// a batch of Meridian events for this organisation, every subscriber hears about it.
export function LiveProvider({ children }) {
  const { user } = useAuth();
  const [status, setStatus] = useState("idle"); // idle | connecting | open | retrying
  const [lastEvent, setLastEvent] = useState(null);
  const listeners = useRef(new Set());

  useEffect(() => {
    if (!user) return undefined;
    let close = () => {};
    let retryTimer;
    let attempt = 0;
    let cancelled = false;

    const connect = () => {
      setStatus(attempt ? "retrying" : "connecting");
      close = streamActivity(
        {},
        ({ event, data }) => {
          if (event !== "ingest") return;
          setLastEvent(data);
          listeners.current.forEach((fn) => fn(data));
        },
        {
          onOpen: () => {
            attempt = 0;
            setStatus("open");
          },
          onError: () => {
            if (cancelled) return;
            attempt += 1;
            setStatus("retrying");
            retryTimer = setTimeout(connect, Math.min(30_000, 1000 * 2 ** attempt));
          },
        },
      );
    };
    connect();
    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
      close();
      setStatus("idle");
    };
  }, [user]);

  const subscribe = useCallback((fn) => {
    listeners.current.add(fn);
    return () => listeners.current.delete(fn);
  }, []);

  const value = useMemo(() => ({ status, lastEvent, subscribe }), [status, lastEvent, subscribe]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive() {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive must be used inside LiveProvider");
  return ctx;
}
