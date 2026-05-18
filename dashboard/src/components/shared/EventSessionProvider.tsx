"use client";

import { useEventSession } from "@/hooks/useEventSession";

export function EventSessionProvider({ children }: { children: React.ReactNode }) {
  useEventSession();
  return <>{children}</>;
}
