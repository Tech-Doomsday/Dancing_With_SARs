"use client";

/** TokenGate (§1) — asks for the background token once, keeps it in sessionStorage (try/catch),
 *  never in the URL, never logged. Shown when a background call answered 401, or 503 with a
 *  message naming NISAR_BACKGROUND_TOKEN (gate unconfigured on the engine). A stopgap: real
 *  authentication is an owner action. */
import { useState } from "react";
import { KeyRound } from "lucide-react";
import { getToken, setToken } from "@/lib/bgApi";

export default function TokenGate({ onRetry, kind }: { onRetry: () => void; kind: "unauthorized" | "unconfigured" }) {
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);

  const submit = () => {
    setToken(value.trim());
    setSaved(true);
    onRetry();
  };

  return (
    <div className="mx-auto mt-10 max-w-md rounded-lg border border-[#4a3a1f] bg-[#1d1810] p-5" role="dialog" aria-label="background token required">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-[#e6c977]">
        <KeyRound size={15} aria-hidden /> Background access
      </h2>
      <p className="mt-2 text-xs leading-relaxed text-[#d9c98a]">
        {kind === "unconfigured"
          ? "The engine answers 503: no gate is configured (the environment variable NISAR_BACKGROUND_TOKEN is unset on the engine side). Ask the owner to set it — this UI cannot invent one."
          : "The Background tab shows internal traces and real data fetches, so the engine requires a token: Authorization: Bearer <NISAR_BACKGROUND_TOKEN>."}
      </p>
      <p className="mt-2 text-[11px] leading-relaxed text-[#a08c5f]">
        The token is kept in sessionStorage for this tab session only, is never placed in a URL and
        never logged. This prompt is a stopgap — real authentication is an owner action.
      </p>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={getToken() ? "replace the stored token" : "NISAR_BACKGROUND_TOKEN value"}
          aria-label="Background token"
          className="flex-1 rounded border border-[#4a3a1f] bg-[#141109] px-2 py-1.5 font-mono text-xs text-[#e6c977] placeholder:text-[#7a6a45] focus:border-[#e6c977] focus:outline-none"
        />
        <button
          type="submit"
          className="rounded bg-[#b08d57] px-3 py-1.5 text-xs font-medium text-[#141109] hover:bg-[#c9a76b]"
        >
          {saved ? "retrying…" : "Save and retry"}
        </button>
      </form>
    </div>
  );
}
