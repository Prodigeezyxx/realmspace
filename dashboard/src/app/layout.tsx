import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter, Sora } from "next/font/google";

import { RootProviders } from "@/components/providers/RootProviders";

import "./globals.css";

// docs/brand.md §2. Three faces with three jobs — Sora for headlines because it
// matches the wordmark, Inter for everything read at small sizes on a dense
// dashboard, and IBM Plex Mono reserved for telemetry so that monospace *means*
// something rather than merely looking technical.
const sora = Sora({
  variable: "--font-sora",
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600", "700", "800"],
});

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
  weight: ["300", "400", "500", "600", "700"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "realmspace — measurement for physical experiences",
  description:
    "The measurement, replay and intelligence layer for physical brand experiences. One camera. One laptop. A queryable graph of attention, dwell and behaviour — plus a 3D digital twin you can scrub through and ask questions of.",
  metadataBase: new URL("https://realmspace.io"),
  openGraph: {
    title: "realmspace — measurement for physical experiences",
    description:
      "The measurement, replay and intelligence layer for physical brand experiences.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${sora.variable} ${inter.variable} ${plexMono.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-bg-base text-text-primary">
        <RootProviders>{children}</RootProviders>
      </body>
    </html>
  );
}
