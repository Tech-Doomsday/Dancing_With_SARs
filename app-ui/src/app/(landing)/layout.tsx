import type { Metadata, Viewport } from "next";
import { Fraunces, Inter } from "next/font/google";
import RootClassSync from "@/components/speckle/RootClassSync";

/**
 * The Speckle cinematic landing — wired into app-ui as the front door ("/").
 *
 * This layout reproduces the original standalone app's root layout
 * (CodexUjayer/speckle): Fraunces + Inter via next/font, the Speckle
 * metadata, and the dark deep-space body. Because the app has ONE root
 * layout, the fonts live on the wrapper element and the dark background
 * is applied by the `.speckle-page` class (scoped CSS in
 * (site)/globals.css) plus `html.speckle-root` (toggled by RootClassSync
 * only while this route is mounted) — so no console page is affected.
 */

const fraunces = Fraunces({
  variable: "--font-speckle-display",
  style: ["normal", "italic"],
  axes: ["opsz"],
  subsets: ["latin"],
  display: "swap",
});

const inter = Inter({
  variable: "--font-speckle-sans",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Speckle · Earth is always changing",
  description:
    "A cinematic journey from orbit into Earth's changing landscapes. Built for NASA Space Apps Challenge 2026, Dancing with the SARs.",
  keywords: [
    "Speckle",
    "Earth observation",
    "SAR",
    "radar",
    "NASA Space Apps",
    "Dancing with the SARs",
    "hazard engine",
  ],
  authors: [{ name: "Dancing with the SARs team" }],
  openGraph: {
    title: "Speckle · Earth is always changing",
    description:
      "A cinematic journey from orbit into Earth's changing landscapes, observed from space and measured in radar.",
    siteName: "Speckle",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Speckle · Earth is always changing",
    description:
      "A cinematic journey from orbit into Earth's changing landscapes.",
  },
};

export const viewport: Viewport = {
  themeColor: "#04070d",
  width: "device-width",
  initialScale: 1,
};

export default function LandingLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className={`${fraunces.variable} ${inter.variable} speckle-page`}>
      <RootClassSync />
      {children}
    </div>
  );
}
