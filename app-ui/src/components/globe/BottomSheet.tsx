"use client";

/** BottomSheet (mobile <1024px): the panel becomes a draggable sheet with three snap points
 *  (peek / half / full). Draggable AND keyboard operable (arrow keys move the snap, Home/End). */
import { useCallback, useEffect, useRef, useState } from "react";

export type Snap = "peek" | "half" | "full";

export default function BottomSheet({
  children,
  ariaLabel,
}: {
  children: React.ReactNode;
  ariaLabel: string;
}) {
  const [snap, setSnap] = useState<Snap>("peek");
  const dragRef = useRef<{ y: number; start: number } | null>(null);
  const [dragOffset, setDragOffset] = useState(0);

  const heightPct = snap === "peek" ? 20 : snap === "half" ? 52 : 88;

  const move = useCallback((dy: number, startY: number) => {
    setDragOffset(Math.max(0, dy));
    void startY;
  }, []);

  const settle = useCallback((dy: number) => {
    setDragOffset(0);
    if (dy > 90) {
      setSnap((s) => (s === "full" ? "half" : "peek"));
    } else if (dy < -90) {
      setSnap((s) => (s === "peek" ? "half" : "full"));
    }
  }, []);

  useEffect(() => {
    setDragOffset(0);
  }, [snap]);

  return (
    <div
      className="fixed inset-x-0 bottom-0 z-40 rounded-t-2xl border-t border-[#e7e2da] bg-[#faf9f6]/97 shadow-[0_-6px_24px_rgba(0,0,0,0.15)] backdrop-blur transition-[height] duration-200"
      style={{
        height: `calc(${heightPct}vh - ${dragOffset}px)`,
        minHeight: 120,
        transitionDuration: dragRef.current ? "0ms" : undefined,
      }}
      role="complementary"
      aria-label={ariaLabel}
    >
      <div
        className="flex cursor-grab touch-none items-center justify-center py-2"
        onPointerDown={(e) => {
          dragRef.current = { y: e.clientY, start: e.clientY };
          (e.target as Element).setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!dragRef.current) return;
          move(e.clientY - dragRef.current.start, dragRef.current.start);
        }}
        onPointerUp={(e) => {
          const drag = dragRef.current;
          dragRef.current = null;
          if (drag) settle(e.clientY - drag.start);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowUp") {
            setSnap((s) => (s === "peek" ? "half" : "full"));
            e.preventDefault();
          } else if (e.key === "ArrowDown") {
            setSnap((s) => (s === "full" ? "half" : "peek"));
            e.preventDefault();
          } else if (e.key === "Home") {
            setSnap("full");
            e.preventDefault();
          } else if (e.key === "End") {
            setSnap("peek");
            e.preventDefault();
          }
        }}
        tabIndex={0}
        role="separator"
        aria-label={`panel height: ${snap}. Arrow up/down to change`}
      >
        <div className="h-1.5 w-12 rounded-full bg-[#d8d2c6]" aria-hidden />
      </div>
      <div className="h-[calc(100%-28px)] overflow-y-auto overscroll-contain">{children}</div>
    </div>
  );
}
