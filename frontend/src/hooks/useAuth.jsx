import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, setCsrf } from "../api/client";
const AuthContext = createContext(null);
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    api
      .get("/auth/me")
      .then((me) => {
        setCsrf(me.csrf_token);
        setUser(me);
      })
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);
  const login = useCallback(async (email, password) => {
    const me = await api.post("/auth/login", { email, password });
    setCsrf(me.csrf_token);
    setUser(me);
    return me;
  }, []);
  const register = useCallback(async (data) => {
    const me = await api.post("/auth/register", data);
    setCsrf(me.csrf_token);
    setUser({ ...me, role: "customer" });
    return { ...me, role: "customer" };
  }, []);
  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setCsrf(null);
      setUser(null);
    }
  }, []);
  return <AuthContext.Provider value={{ user, loading, login, register, logout }}>{children}</AuthContext.Provider>;
}
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
export function homeFor(user) {
  return user.role === "customer" ? "/tickets" : "/console";
}
