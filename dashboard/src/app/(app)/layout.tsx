import { ReactNode } from "react";

import { NavRail } from "@/components/chrome/NavRail";
import { StatusBar } from "@/components/chrome/StatusBar";
import { LiveSessionProvider } from "@/components/live/LiveSessionProvider";
import { EventSessionProvider } from "@/components/shared/EventSessionProvider";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <EventSessionProvider>
    <LiveSessionProvider>
      <div className="realm-app-shell surface">
        <StatusBar />
        <div className="realm-workspace">
          <NavRail />
          <main className="realm-main surface-container-lowest">
            {children}
          </main>
        </div>
      </div>
    </LiveSessionProvider>
    </EventSessionProvider>
  );
}
