"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { onAuthStateChanged, type User } from "firebase/auth";

import { getFirebaseAuth } from "@/lib/firebase/client";
import { isFirebaseConfigured } from "@/lib/firebase/config";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  configured: boolean;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  configured: false,
});

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const configured = isFirebaseConfigured();
  // getFirebaseAuth() returns a cached module-level singleton (or null when
  // unconfigured) — safe to read during render, unlike the subscription below.
  const auth = configured ? getFirebaseAuth() : null;
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(() => !!auth);

  useEffect(() => {
    if (!auth) return;
    // setState only inside the subscription callback — never synchronously
    // in the effect body — so this stays a pure "sync external system" effect.
    return onAuthStateChanged(auth, (next) => {
      setUser(next);
      setLoading(false);
    });
  }, [auth]);

  const value = useMemo(
    () => ({ user, loading, configured }),
    [user, loading, configured]
  );

  return (
    <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
  );
}
