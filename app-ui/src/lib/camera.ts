/** Map camera memory — the globe remembers where it was looking, per tab session, so switching
 *  Map -> Background -> Map (or following "Show on globe") never resets to the world view.
 *  Pure helpers + sessionStorage (every access try/catch: private mode and blocked storage must
 *  never break the map). Nothing here is science; it stores the camera the user left. */

export interface Camera {
  center: [number, number]; // [lon, lat]
  zoom: number;
  projection: "globe" | "mercator";
}

export const DEFAULT_CAMERA: Camera = { center: [0, 20], zoom: 1.4, projection: "globe" };
/** zoom used when a link names a point but no zoom */
export const DEFAULT_POINT_ZOOM = 10.5;

const KEY = "nisar:map-camera:v1";

export function sanitizeCamera(raw: unknown): Camera | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const c = r.center;
  if (!Array.isArray(c) || c.length !== 2) return null;
  const [lon, lat] = c as unknown[];
  const zoom = r.zoom;
  if (typeof lon !== "number" || typeof lat !== "number" || typeof zoom !== "number") return null;
  if (!Number.isFinite(lon) || !Number.isFinite(lat) || !Number.isFinite(zoom)) return null;
  if (lon < -180 || lon > 180 || lat < -90 || lat > 90 || zoom < 0 || zoom > 24) return null;
  return { center: [lon, lat], zoom, projection: r.projection === "mercator" ? "mercator" : "globe" };
}

export function loadCamera(): Camera | null {
  try {
    const txt = window.sessionStorage.getItem(KEY);
    return txt ? sanitizeCamera(JSON.parse(txt)) : null;
  } catch {
    return null;
  }
}

export function saveCamera(cam: Camera): void {
  try {
    const ok = sanitizeCamera(cam);
    if (ok) window.sessionStorage.setItem(KEY, JSON.stringify(ok));
  } catch { /* storage unavailable: the camera is simply not remembered */ }
}

/** Where a new map must be created. A link that names a point and a zoom wins (the user asked
 *  to go there); otherwise the camera the user left; otherwise the world view. */
export function initialCamera(
  url: { lat?: number; lon?: number; z?: number },
  saved: Camera | null,
): { camera: Camera; source: "url" | "saved" | "default" } {
  // a point WITHOUT a zoom (the nav's Map link) must not undo the view the user left
  if (url.lat !== undefined && url.lon !== undefined && !(url.z === undefined && saved)) {
    return {
      camera: { center: [url.lon, url.lat], zoom: url.z ?? DEFAULT_POINT_ZOOM, projection: saved?.projection ?? "globe" },
      source: "url",
    };
  }
  if (saved) return { camera: saved, source: "saved" };
  return { camera: DEFAULT_CAMERA, source: "default" };
}
