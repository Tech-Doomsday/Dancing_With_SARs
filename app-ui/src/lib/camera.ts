/** Last map camera, kept per browser tab so switching Map <-> Background never resets the view.
 *  Only a view the user (or a link) chose is stored; everything read back is re-validated. */
export interface Camera { center: [number, number]; zoom: number }

const KEY = "nisar:camera:v1";
export const DEFAULT_CAMERA: Camera = { center: [0, 20], zoom: 1.4 };

function valid(c: unknown): c is Camera {
  if (!c || typeof c !== "object") return false;
  const { center, zoom } = c as Camera;
  return Array.isArray(center) && center.length === 2
    && Number.isFinite(center[0]) && Number.isFinite(center[1])
    && Math.abs(center[0]) <= 180 && Math.abs(center[1]) <= 90
    && Number.isFinite(zoom) && zoom >= 0 && zoom <= 24;
}

export function loadCamera(): Camera | null {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return null;
    const c = JSON.parse(raw);
    return valid(c) ? c : null;
  } catch { return null; }
}

export function saveCamera(c: Camera): void {
  if (!valid(c)) return;
  try { window.sessionStorage.setItem(KEY, JSON.stringify(c)); } catch { /* private mode */ }
}

/** A ?lat=&lon=&z= link names its own camera (zoom defaults to 10.5). Takes precedence over the saved one. */
export function cameraFromParams(p: URLSearchParams): Camera | null {
  const lat = Number(p.get("lat")), lon = Number(p.get("lon"));
  if (p.get("lat") === null || p.get("lon") === null) return null;
  const z = p.get("z") === null ? 10.5 : Number(p.get("z"));
  const c = { center: [lon, lat] as [number, number], zoom: z };
  return valid(c) ? c : null;
}

/** What the map should be created at: link > saved > world. Safe on the server (returns world). */
export function initialCamera(p: URLSearchParams): Camera {
  if (typeof window === "undefined") return DEFAULT_CAMERA;
  return cameraFromParams(p) ?? loadCamera() ?? DEFAULT_CAMERA;
}
