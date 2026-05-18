import { ReactNode } from "react";

import { NavRail } from "@/components/chrome/NavRail";
import { StatusBar } from "@/components/chrome/StatusBar";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col canvas-vignette">
      <StatusBar />
      <div className="flex flex-1 min-h-0">
        <NavRail />
        <main className="flex-1 min-w-0 overflow-x-hidden">{children}</main>
      </div>
    </div>
  );
}
