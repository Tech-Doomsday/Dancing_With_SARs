/** Browser geolocation wrapper (DEC2 + S4 behaviour contract). The position never leaves the
 *  browser except as the coordinates the user chooses to scan. Pure branches, unit-tested with
 *  the REAL function signatures via injected fakes.
 *
 *  Contract (verified against the web platform):
 *  - navigator.permissions.query({name:'geolocation'}) → PermissionStatus.state in
 *    'granted' | 'prompt' | 'denied'; Safari ≤16 REJECTS the query → treated as 'prompt'
 *    (the control shows and the browser's own dialog decides).
 *  - No navigator.permissions or no navigator.geolocation (insecure context) → 'unavailable'.
 *  - getCurrentPosition({enableHighAccuracy:false, timeout:8000, maximumAge:600000}):
 *      success → {lat, lon} once; error.code 1/2/3 → denied / unavailable / timeout messages.
 *      No retry loop, ever. */

export type GeoState = "granted" | "prompt" | "denied" | "unavailable";

export interface GeoPosition {
  lat: number;
  lon: number;
}

type PermissionsLike = {
  query: (d: { name: "geolocation" }) => Promise<{ state: string }>;
};
type GeolocationLike = {
  getCurrentPosition: (
    success: (pos: { coords: { latitude: number; longitude: number } }) => void,
    error?: (err: { code: number; message?: string }) => void,
    options?: { enableHighAccuracy?: boolean; timeout?: number; maximumAge?: number },
  ) => void;
};

export interface GeoNav {
  permissions?: PermissionsLike;
  geolocation?: GeolocationLike;
}

/** synchronous best-effort state from the Permissions API (never throws, never prompts) */
export async function geoState(nav: GeoNav): Promise<GeoState> {
  if (!nav.geolocation) return "unavailable";
  if (!nav.permissions?.query) return "prompt"; // e.g. Safari: query unsupported → let the dialog decide
  try {
    const status = await nav.permissions.query({ name: "geolocation" });
    if (status.state === "granted") return "granted";
    if (status.state === "denied") return "denied";
    return "prompt";
  } catch {
    return "prompt";
  }
}

export type GeoOutcome =
  | { ok: true; position: GeoPosition }
  | { ok: false; reason: "denied" | "unavailable" | "timeout" | "error"; message: string };

/** one-shot getCurrentPosition with the documented options and plain-language failures */
export function currentPosition(nav: GeoNav): Promise<GeoOutcome> {
  return new Promise((resolve) => {
    if (!nav.geolocation) {
      resolve({ ok: false, reason: "unavailable", message: "this browser cannot provide your location (it needs a secure context)" });
      return;
    }
    nav.geolocation.getCurrentPosition(
      (pos) => resolve({ ok: true, position: { lat: pos.coords.latitude, lon: pos.coords.longitude } }),
      (err) => {
        if (err.code === 1) {
          resolve({ ok: false, reason: "denied", message: "location permission was denied — you can still type coordinates" });
        } else if (err.code === 3) {
          resolve({ ok: false, reason: "timeout", message: "took too long to get your position — try again or type coordinates" });
        } else if (err.code === 2) {
          resolve({ ok: false, reason: "unavailable", message: "your position is unavailable right now — you can still type coordinates" });
        } else {
          resolve({ ok: false, reason: "error", message: err.message || "location failed" });
        }
      },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 600_000 },
    );
  });
}
