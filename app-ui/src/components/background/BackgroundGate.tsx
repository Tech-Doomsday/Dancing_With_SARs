"use client";

/** BackgroundGate (§1) — context provider for the gated Background tab. Pages call useGateFail()
 *  when a background API call answers 401 / gate-503; the provider renders the token prompt as
 *  an OVERLAY (children stay mounted underneath) and bumps retryNonce on save so pages'
 *  effects can re-run. The gate itself is enforced by the ENGINE; this is only the prompt UI. */
import { createContext, useCallback, useContext, useState } from "react";
import TokenGate from "./TokenGate";

type GateState = {
  gateFail: (kind: "unauthorized" | "unconfigured") => void;
  retryNonce: number;               // increments on every successful token save
  failed: boolean;
};

const GateContext = createContext<GateState>({
  gateFail: () => undefined,
  retryNonce: 0,
  failed: false,
});

export function useGateFail() {
  return useContext(GateContext).gateFail;
}

/** pages include this in their data-effect deps so a saved token re-triggers the fetch */
export function useGateRetryNonce(): number {
  return useContext(GateContext).retryNonce;
}

export function useGateFailed(): boolean {
  return useContext(GateContext).failed;
}

export default function BackgroundGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<{ failed: boolean; kind: "unauthorized" | "unconfigured"; nonce: number }>({
    failed: false, kind: "unauthorized", nonce: 0,
  });
  const gateFail = useCallback((kind: "unauthorized" | "unconfigured") => {
    setState((s) => (s.failed ? s : { ...s, failed: true, kind }));
  }, []);
  const retry = useCallback(() => {
    setState((s) => ({ failed: false, kind: s.kind, nonce: s.nonce + 1 }));
  }, []);

  return (
    <GateContext.Provider value={{ gateFail, retryNonce: state.nonce, failed: state.failed }}>
      <div className="relative flex min-h-0 flex-1 flex-col">
        {children}
        {state.failed && (
          <div className="absolute inset-0 z-[70] overflow-y-auto bg-[#0e1116]/95">
            <TokenGate kind={state.kind} onRetry={retry} />
          </div>
        )}
      </div>
    </GateContext.Provider>
  );
}
