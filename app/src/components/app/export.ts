/**
 * Client-side export of analysis rasters: GeoTIFF, CSV and PNG.
 *
 * Everything is generated in the browser from the decoded transport grids
 * (which the map already renders), so exports work for any displayed result
 * without a second server round-trip — including the evidence-mode model
 * prediction (clearly labelled as such in the file names).
 *
 * The GeoTIFF writer emits a minimal but standard single-band float32 TIFF:
 *   - IMAGEWIDTH/LENGTH, BITSPERSAMPLE 32, SAMPLEFORMAT 3 (IEEE float)
 *   - one strip, little-endian
 *   - ModelPixelScale + ModelTiepoint mapping pixel (0,0) to the UTM corner
 *   - GeoKeyDirectory with ProjectedCSTypeGeoKey = the grid's EPSG code
 *   - GDAL_NODATA = "nan" so masked pixels read as no-data in QGIS/GDAL
 */

import { utmToLatLon } from '@/lib/science/utm';

export interface ExportGridMeta {
  xStart: number;
  xSpacing: number;
  yStart: number;
  ySpacing: number;
  width: number;
  height: number;
  epsg: number;
}

/** TIFF tag ids used below. */
const TAG = {
  IMAGEWIDTH: 256,
  IMAGELENGTH: 257,
  BITSPERSAMPLE: 258,
  COMPRESSION: 259,
  PHOTOMETRIC: 262,
  STRIPOFFSETS: 273,
  SAMPLESPERPIXEL: 277,
  ROWSPERSTRIP: 278,
  STRIPBYTECOUNTS: 279,
  PLANARCONFIG: 284,
  SAMPLEFORMAT: 339,
  MODELPIXELSCALE: 33550,
  MODELTIEPOINT: 33922,
  GEOKEYDIRECTORY: 34735,
  GDAL_NODATA: 42113,
} as const;

const TYPE = { SHORT: 3, LONG: 4, DOUBLE: 12 } as const;

class ByteWriter {
  private buf: ArrayBuffer;
  private view: DataView;
  pos = 0;
  constructor(size: number) {
    this.buf = new ArrayBuffer(size);
    this.view = new DataView(this.buf);
  }
  u16(v: number) { this.view.setUint16(this.pos, v, true); this.pos += 2; }
  u32(v: number) { this.view.setUint32(this.pos, v, true); this.pos += 4; }
  f32(v: number) { this.view.setFloat32(this.pos, v, true); this.pos += 4; }
  f64(v: number) { this.view.setFloat64(this.pos, v, true); this.pos += 8; }
  ascii(s: string) { for (let i = 0; i < s.length; i++) this.view.setUint8(this.pos + i, s.charCodeAt(i)); this.pos += s.length; this.view.setUint8(this.pos, 0); this.pos += 1; }
  get bytes(): Uint8Array { return new Uint8Array(this.buf); }
  /** The full underlying ArrayBuffer (for Blob construction). */
  get buffer(): ArrayBuffer { return this.buf; }
  get length(): number { return this.buf.byteLength; }
}

/**
 * Encode a float32 raster + grid metadata as a GeoTIFF blob.
 * NaN values are preserved (IEEE NaN) and declared as GDAL no-data.
 */
export function toGeoTiff(
  data: Float32Array,
  meta: ExportGridMeta,
  description: string,
): Blob {
  const { width, height } = meta;
  const n = width * height;
  if (data.length !== n) throw new Error(`grid size mismatch: ${data.length} != ${n}`);

  // ---- payload layout ----
  // header(8) + IFD entries + external arrays + pixel strip
  const numTags = 15;
  const ifdOffset = 8;
  const ifdSize = 2 + numTags * 12 + 4;
  let cursor = ifdOffset + ifdSize;

  // External value blocks (each 8-byte aligned for doubles).
  const pixelScaleOff = cursor; cursor += 3 * 8;
  const tiePointOff = cursor; cursor += 6 * 8;
  // GeoKeyDirectory: header (4 u16) + 5 keys x (4 u16) = 24 u16 = 48 bytes.
  const geoKeyU16 = 4 + 5 * 4;
  const geoKeyOff = cursor; cursor += geoKeyU16 * 2;
  const nodataOff = cursor; cursor += 4; // "nan\0"
  const descOff = cursor; cursor += description.length + 1;
  const stripOff = cursor; cursor += n * 4;

  const w = new ByteWriter(cursor);
  // TIFF header (little endian).
  w.u16(0x4949); w.u16(42); w.u32(ifdOffset);
  if (w.pos !== ifdOffset) throw new Error('header size drift');

  // ---- IFD ----
  w.u16(numTags);
  const entry = (tag: number, type: number, count: number, valueOrOffset: number) => {
    w.u16(tag); w.u16(type); w.u32(count);
    // For SHORT values the value fits inline (left-justified in the 4-byte field).
    if (type === TYPE.SHORT && count === 1) { w.u16(valueOrOffset); w.u16(0); }
    else { w.u32(valueOrOffset); }
  };
  entry(TAG.IMAGEWIDTH, TYPE.LONG, 1, width);
  entry(TAG.IMAGELENGTH, TYPE.LONG, 1, height);
  entry(TAG.BITSPERSAMPLE, TYPE.SHORT, 1, 32);
  entry(TAG.COMPRESSION, TYPE.SHORT, 1, 1); // none
  entry(TAG.PHOTOMETRIC, TYPE.SHORT, 1, 1); // min-is-black
  entry(TAG.STRIPOFFSETS, TYPE.LONG, 1, stripOff);
  entry(TAG.SAMPLESPERPIXEL, TYPE.SHORT, 1, 1);
  entry(TAG.ROWSPERSTRIP, TYPE.LONG, 1, height);
  entry(TAG.STRIPBYTECOUNTS, TYPE.LONG, 1, n * 4);
  entry(TAG.PLANARCONFIG, TYPE.SHORT, 1, 1);
  entry(TAG.SAMPLEFORMAT, TYPE.SHORT, 1, 3); // IEEE float
  entry(TAG.MODELPIXELSCALE, TYPE.DOUBLE, 3, pixelScaleOff);
  entry(TAG.MODELTIEPOINT, TYPE.DOUBLE, 6, tiePointOff);
  entry(TAG.GEOKEYDIRECTORY, TYPE.SHORT, geoKeyU16, geoKeyOff);
  entry(TAG.GDAL_NODATA, 2, 4, nodataOff); // ASCII "nan"
  // NOTE: description lives in GDAL_METADATA normally; we keep 14 tags and
  // drop the description tag to stay minimal — the file NAME carries it.
  w.u32(0); // next IFD = none
  if (w.pos !== pixelScaleOff) throw new Error(`ifd size drift: ${w.pos} != ${pixelScaleOff}`);

  // ---- ModelPixelScale: (sx, sy, sz) ----
  w.f64(meta.xSpacing);
  w.f64(Math.abs(meta.ySpacing));
  w.f64(1);

  // ---- ModelTiepoint: raster (0,0,0) -> map (xWest, yNorth, 0) ----
  // Tie the OUTER corner (pixel-centre minus half spacing) as GDAL expects.
  w.f64(0); w.f64(0); w.f64(0);
  w.f64(meta.xStart - meta.xSpacing / 2);
  w.f64(meta.yStart - meta.ySpacing / 2); // yStart is the northern centre; -spacing/2 -> outer
  w.f64(0);

  // ---- GeoKeyDirectory ----
  // Header: directory version 1, revision 1.0, then the number of keys.
  const K = { GTModelType: 1024, GTRasterType: 1025, GeographicType: 2048, ProjectedCSType: 3072, ProjLinearUnits: 3051 } as const;
  w.u16(1); w.u16(1); w.u16(0); w.u16(5);
  w.u16(K.GTModelType); w.u16(0); w.u16(1); w.u16(1);          // ModelTypeProjected
  w.u16(K.GTRasterType); w.u16(0); w.u16(1); w.u16(2);         // RasterPixelIsArea
  w.u16(K.ProjLinearUnits); w.u16(0); w.u16(1); w.u16(9001);   // metres
  w.u16(K.ProjectedCSType); w.u16(0); w.u16(1); w.u16(meta.epsg);
  w.u16(K.GeographicType); w.u16(0); w.u16(1); w.u16(4326);    // WGS84 geographic (via EPSG)
  if (w.pos !== geoKeyOff + geoKeyU16 * 2) throw new Error('geokey size drift');

  // ---- GDAL_NODATA ascii ----
  w.ascii('nan');

  // ---- description ascii (skipped in tags; kept for byte alignment only) ----
  w.ascii(description);

  // ---- pixel strip (float32, NaN preserved) ----
  for (let i = 0; i < n; i++) w.f32(data[i]);
  if (w.pos !== cursor) throw new Error(`strip size drift: ${w.pos} != ${cursor}`);

  return new Blob([w.buffer], { type: 'image/tiff' });
}

/** Sample grid to UTM easting/northing + value rows for CSV export. */
export function toCsv(
  data: Float32Array,
  mask: Uint8Array,
  meta: ExportGridMeta,
  layerName: string,
): Blob {
  const lines: string[] = [
    `# MOTUS export — ${layerName}`,
    `# grid: ${meta.width}x${meta.height} @ ${meta.xSpacing} m, EPSG:${meta.epsg}`,
    `# value: metres (NaN = no valid measurement at this pixel)`,
    'easting_m,northing_m,lat,lon,value_m',
  ];
  const zone = meta.epsg > 32600 && meta.epsg <= 32660 ? meta.epsg - 32600 : meta.epsg - 32700;
  const southern = meta.epsg > 32700;
  const step = Math.max(1, Math.floor(Math.max(meta.width, meta.height) / 1400)); // cap rows
  for (let i = 0; i < meta.height; i += step) {
    const northing = meta.yStart + i * meta.ySpacing;
    for (let j = 0; j < meta.width; j += step) {
      const easting = meta.xStart + j * meta.xSpacing;
      const gi = i * meta.width + j;
      if (mask && !mask[gi]) continue;
      const v = data[gi];
      const ll = utmToLatLon(easting, northing, zone, southern);
      const vs = Number.isFinite(v) ? v.toFixed(6) : 'NaN';
      lines.push(`${easting.toFixed(1)},${northing.toFixed(1)},${ll.lat.toFixed(6)},${ll.lon.toFixed(6)},${vs}`);
    }
  }
  return new Blob([lines.join('\n')], { type: 'text/csv' });
}

/** Trigger a browser download for any blob. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/** Slugify a dataset id for file names. */
export function fileStem(datasetId: string, layer: string, prediction = false): string {
  const stem = datasetId.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '');
  return `motus-${stem}${prediction ? '-model-prediction' : ''}-${layer}`;
}
