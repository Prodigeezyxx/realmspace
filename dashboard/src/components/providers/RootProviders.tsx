"use client";

import type { ReactNode } from "react";

import { AuthGate } from "@/components/auth/AuthGate";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { BusBridge } from "@/components/chrome/BusPill";

export function RootProviders({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      {/* Inside AuthProvider: the bridge needs the signed-in user's email to
          exchange for a bus token. Outside AuthGate, so the feed stays open
          across whatever the gate renders. */}
      <BusBridge />
      <AuthGate>{children}</AuthGate>
    </AuthProvider>
  );
}
