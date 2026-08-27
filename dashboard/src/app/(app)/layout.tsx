import { ReactNode } from "react";

import { FirstRunGate } from "@/components/chrome/FirstRunGate";
import { NavRail } from "@/components/chrome/NavRail";
import { StatusBar } from "@/components/chrome/StatusBar";
import { LiveSessionProvider } from "@/components/live/LiveSessionProvider";
import { EventSessionProvider } from "@/components/shared/EventSessionProvider";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <EventSessionProvider>
    <LiveSessionProvider>
      <div className="min-h-screen flex flex-col canvas-vignette">
        <StatusBar />
        <div className="flex flex-1 min-h-0">
          <NavRail />
          <main className="flex-1 min-w-0 overflow-x-hidden">
            <FirstRunGate>{children}</FirstRunGate>
          </main>
        </div>
      </div>
    </LiveSessionProvider>
    </EventSessionProvider>
  );
}
