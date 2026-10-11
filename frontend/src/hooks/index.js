import { useEffect, useRef, useState } from "react";
import { getPatient } from "../api/client";
import { useLive } from "../context/LiveContext";
import { useApi } from "./useApi";

export { useApi } from "./useApi";

// One patient's record. Refreshes quietly when the live feed reports new data for them.
export function usePatient(patientId) {
  const result = useApi(() => getPatient(patientId), [patientId], { keepPrevious: true });
  useLiveRefresh(result.refresh, { patientId });
  return result;
}

// Call `onChange` when the live feed applies data matching the filter.
//   useLiveRefresh(refresh, { facilityId })   -> anything at that facility (null = anywhere)
//   useLiveRefresh(refresh, { patientId })    -> anything about that patient
// Calls are throttled so a burst of batches causes one refresh.
export function useLiveRefresh(onChange, { facilityId = null, patientId = null, throttleMs = 4000 } = {}) {
  const { subscribe } = useLive();
  const latest = useRef(onChange);
  latest.current = onChange;

  useEffect(() => {
    let timer = null;
    return subscribe((event) => {
      if (facilityId !== null && event.facility_id !== facilityId) return;
      if (patientId !== null && !event.patient_ids?.includes(Number(patientId))) return;
      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        latest.current();
      }, throttleMs);
    });
  }, [subscribe, facilityId, patientId, throttleMs]);
}

export function useDebounced(value, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

// Re-render every `ms` so relative times ("3 min ago") stay current.
export function useNow(ms = 30_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export function useDocumentTitle(title) {
  useEffect(() => {
    document.title = title ? `${title} · Atlas` : "Atlas";
  }, [title]);
}
