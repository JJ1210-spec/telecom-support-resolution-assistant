import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setCsrf } from "../api/client";
import type { User } from "../api/types";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (data: { email: string; password: string; name: string; region?: string }) => Promise<User>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<User & { csrf_token: string }>("/auth/me")
      .then((me) => {
        setCsrf(me.csrf_token);
        setUser(me);
      })
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const me = await api.post<User & { csrf_token: string }>("/auth/login", { email, password });
    setCsrf(me.csrf_token);
    setUser(me);
    return me;
  }, []);

  const register = useCallback(async (data: { email: string; password: string; name: string; region?: string }) => {
    const me = await api.post<User & { csrf_token: string }>("/auth/register", data);
    setCsrf(me.csrf_token);
    setUser({ ...me, role: "customer" });
    return { ...me, role: "customer" as const };
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

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

export function homeFor(user: User): string {
  return user.role === "customer" ? "/tickets" : "/console";
}
