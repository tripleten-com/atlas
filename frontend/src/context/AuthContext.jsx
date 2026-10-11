import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { SESSION_EXPIRED_EVENT, getMe, getToken, login as apiLogin, setToken } from "../api/client";

const AuthContext = createContext(null);

// Holds the signed-in user. The token itself lives in localStorage (see api/client.js).
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState(getToken() ? "checking" : "signed-out");
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    if (!getToken()) return;
    getMe()
      .then((me) => {
        setUser(me);
        setStatus("signed-in");
      })
      .catch(() => {
        setToken(null);
        setStatus("signed-out");
      });
  }, []);

  useEffect(() => {
    const expire = () => {
      setToken(null);
      setUser(null);
      setStatus("signed-out");
      setNotice("Your session ended. Sign in again to continue.");
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, expire);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, expire);
  }, []);

  const login = useCallback(async (email, password) => {
    const result = await apiLogin(email, password);
    setToken(result.access_token);
    setUser(result.user);
    setNotice(null);
    setStatus("signed-in");
    return result.user;
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    setStatus("signed-out");
  }, []);

  const value = useMemo(
    () => ({ user, status, notice, login, logout, isAdmin: user?.role === "admin" }),
    [user, status, notice, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
