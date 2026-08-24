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
  const [user, setUser] = useState<User | null>(null);
  const configured = isFirebaseConfigured();

  // `loading` is **derived**, not stored, and that is the whole of the fix here.
  // It used to start at `true` and be corrected inside the effect — twice, on
  // two different branches — so a deployment with no Firebase rendered a
  // loading state for one pass and then set state to take it back. A cascading
  // render for an answer that was knowable before the first one.
  //
  // There is exactly one thing here we genuinely have to wait for: Firebase
  // telling us who is signed in. So that is the only thing kept in state, and
  // it is set from the callback rather than from the effect body.
  const [auth] = useState(() => (isFirebaseConfigured() ? getFirebaseAuth() : null));
  const [resolved, setResolved] = useState(false);
  const loading = auth !== null && !resolved;

  useEffect(() => {
    // No auth to subscribe to — unconfigured, or configured and unavailable.
    // Both are already `loading: false` above, with nothing to announce.
    if (!auth) return;

    return onAuthStateChanged(auth, (next) => {
      setUser(next);
      setResolved(true);
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
