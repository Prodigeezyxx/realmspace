"use client";

import type { ReactNode } from "react";

import { AuthGate } from "@/components/auth/AuthGate";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { useRemoteBusBridgeMount } from "@/hooks/useRemoteBusBridge";

/** Mounts the optional edge-bus WebSocket bridge inside the client tree. */
function RemoteBusBridge() {
  useRemoteBusBridgeMount();
  return null;
}

export function RootProviders({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <AuthGate>
        <RemoteBusBridge />
        {children}
      </AuthGate>
    </AuthProvider>
  );
}
