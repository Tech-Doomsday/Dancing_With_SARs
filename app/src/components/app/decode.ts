/**
 * Base64 → typed-array decoding for transport grids (browser-safe).
 */

export function b64ToUint8(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function b64ToFloat32(b64: string): Float32Array {
  const u8 = b64ToUint8(b64);
  return new Float32Array(u8.buffer, u8.byteOffset, u8.byteLength / 4);
}

export interface DecodedGrids {
  width: number;
  height: number;
  displacement: Float32Array;
  coherence: Float32Array;
  mask: Uint8Array;
  sigma: Float32Array;
  /**
   * Detected-change mask (2026-10-24): 1 = the server-side rule
   * (|d| > max(k·σ, floor)) passes. Zero-filled when the transport omits the
   * field (prediction/velocity transports carry no detection rule).
   */
  detected: Uint8Array;
}

export function decodeTransport(t: {
  width: number;
  height: number;
  displacement: string;
  coherence: string;
  mask: string;
  sigma: string;
  detected?: string;
}): DecodedGrids {
  return {
    width: t.width,
    height: t.height,
    displacement: b64ToFloat32(t.displacement),
    coherence: b64ToFloat32(t.coherence),
    mask: b64ToUint8(t.mask),
    sigma: b64ToFloat32(t.sigma),
    // Tolerant decode: older/prediction transports have no detected grid.
    detected: t.detected ? b64ToUint8(t.detected) : new Uint8Array(t.width * t.height),
  };
}
