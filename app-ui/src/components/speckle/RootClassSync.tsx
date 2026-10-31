"use client";

/**
 * Wiring glue for the integrated Speckle landing.
 *
 * While the landing route is mounted, <html> is tagged with `speckle-root`
 * so the scoped Speckle CSS (appended to (site)/globals.css) can style the
 * document-level chrome — root scrollbar, text selection, color-scheme and
 * body background — exactly like the standalone site did. The class is
 * removed on unmount, so console pages never see any of it.
 */
import { useEffect } from "react";

export default function RootClassSync() {
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add("speckle-root");
    return () => root.classList.remove("speckle-root");
  }, []);
  return null;
}
