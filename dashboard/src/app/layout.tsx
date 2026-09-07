import type { Metadata } from "next";
import { JetBrains_Mono, Manrope } from "next/font/google";

import { RootProviders } from "@/components/providers/RootProviders";

import "./globals.css";

const manrope = Manrope({
  variable: "--font-manrope",
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600", "700", "800"],
});

const jetBrainsMono = JetBrains_Mono({
  variable: "--font-mono-jb",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "RealmSpace — Measurement for Physical Experiences",
  description:
    "The measurement, replay and intelligence layer for physical brand experiences. One camera. One laptop. A queryable graph of attention, dwell and behaviour — plus a 3D digital twin you can scrub through and ask questions of.",
  metadataBase: new URL("https://realmspace.io"),
  openGraph: {
    title: "RealmSpace — Measurement for Physical Experiences",
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
      data-design="signal-room-2026"
      className={`${manrope.variable} ${jetBrainsMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&family=Material+Symbols+Sharp:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=block" />
      </head>
      <body className="min-h-full surface body-medium">
        <RootProviders>{children}</RootProviders>
      </body>
    </html>
  );
}
