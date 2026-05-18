import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const jetBrainsMono = JetBrains_Mono({
  variable: "--font-mono-jb",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "RealmSpace — Experiential Intelligence",
  description:
    "Plug in any camera. Watch the room think. RealmSpace turns physical activations into a queryable graph of attention, dwell, and behavior — with a 3D digital twin you can replay and ask questions of.",
  metadataBase: new URL("https://realmspace.io"),
  openGraph: {
    title: "RealmSpace — Experiential Intelligence",
    description:
      "The measurement, replay, and intelligence layer for physical brand experiences.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetBrainsMono.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-bg-base text-text-primary">
        {children}
      </body>
    </html>
  );
}
