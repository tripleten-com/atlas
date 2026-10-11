import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { getFacilities } from "../api/client";
import { useAuth } from "./AuthContext";

const FacilityContext = createContext(null);

// Which facility the user is looking at. `facilityId === null` means "all facilities".
// Pages pass `facilityId` to the API as the `facility_id` query parameter.
export function FacilityProvider({ children }) {
  const { user } = useAuth();
  const storageKey = user ? `atlas.facility.${user.tenant.slug}` : null;
  const [facilities, setFacilities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [facilityId, setFacilityIdState] = useState(null);

  const refresh = useCallback(() => {
    if (!user) return Promise.resolve();
    return getFacilities()
      .then((list) => setFacilities(list))
      .finally(() => setLoading(false));
  }, [user]);

  useEffect(() => {
    if (!user) {
      setFacilities([]);
      return;
    }
    setLoading(true);
    getFacilities()
      .then((list) => {
        setFacilities(list);
        const saved = localStorage.getItem(storageKey);
        if (saved === "all") setFacilityIdState(null);
        else if (saved && list.some((f) => String(f.id) === saved)) setFacilityIdState(Number(saved));
        else setFacilityIdState(list.find((f) => f.kind === "demo")?.id ?? list[0]?.id ?? null);
      })
      .finally(() => setLoading(false));
  }, [user, storageKey]);

  // Patient counts and feed status change as data streams in; refresh them now and then.
  useEffect(() => {
    if (!user) return undefined;
    const timer = setInterval(refresh, 60_000);
    return () => clearInterval(timer);
  }, [user, refresh]);

  const setFacilityId = useCallback(
    (id) => {
      setFacilityIdState(id);
      if (storageKey) localStorage.setItem(storageKey, id === null ? "all" : String(id));
    },
    [storageKey],
  );

  const value = useMemo(() => {
    const facility = facilities.find((f) => f.id === facilityId) || null;
    return {
      facilities,
      loading,
      facilityId,
      facility,
      setFacilityId,
      refresh,
      scopeLabel: facility ? facility.name : "All facilities",
      isStatic: facility?.data_source === "seed",
    };
  }, [facilities, loading, facilityId, setFacilityId, refresh]);

  return <FacilityContext.Provider value={value}>{children}</FacilityContext.Provider>;
}

export function useFacility() {
  const ctx = useContext(FacilityContext);
  if (!ctx) throw new Error("useFacility must be used inside FacilityProvider");
  return ctx;
}
