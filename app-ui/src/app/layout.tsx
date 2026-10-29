import type { Metadata } from "next";
import "./(site)/globals.css";

export const metadata: Metadata = {
  title: "Dancing with SARs — see what the ground is doing",
  description:
    "A 3D globe over the nisar-hazard-engine: pick a place, scan it, and read every SAR-derived finding the engine can honestly defend — plus the raw granule timeline behind it.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
