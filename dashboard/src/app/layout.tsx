import type { Metadata } from "next";
import { JetBrains_Mono, Plus_Jakarta_Sans } from "next/font/google";

import { RootProviders } from "@/components/providers/RootProviders";

import "./globals.css";

const jakarta = Plus_Jakarta_Sans({
  variable: "--font-jakarta",
  subsets: ["latin"],
  display: "swap",
  weight: ["300", "400", "500", "600", "700", "800"],
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
      className={`${jakarta.variable} ${jetBrainsMono.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-bg-base text-text-primary">
        <RootProviders>{children}</RootProviders>
      </body>
    </html>
  );
}
