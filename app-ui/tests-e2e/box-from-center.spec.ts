/** Centre + range -> box. Pure node. The Dhaka box from the bug report is the reference. */
import { test, expect } from "@playwright/test";
import { boxFromCenter, centerOfBox } from "../src/lib/boxFromCenter";

test("Dhaka 10 km box: centre + range reproduces the reported W/S/E/N", () => {
  const b = boxFromCenter(23.7142, 90.4009, 10.23)!;
  const want = [90.351, 23.6682, 90.4508, 23.7601];
  b.forEach((v, i) => expect(Math.abs(v - want[i])).toBeLessThan(0.002));
});

test("the box is square in km: east-west and north-south extents match", () => {
  const [w, s, e, n] = boxFromCenter(60, 10, 20)!;
  const nsKm = (n - s) * 111.32;
  const ewKm = (e - w) * 111.32 * Math.cos((60 * Math.PI) / 180);
  expect(Math.abs(nsKm - 20)).toBeLessThan(0.01);
  expect(Math.abs(ewKm - 20)).toBeLessThan(0.01);
});

test("round trip through centerOfBox", () => {
  const b = boxFromCenter(-33.9, 151.2, 40)!;
  const c = centerOfBox(b);
  expect(Math.abs(c.lat + 33.9)).toBeLessThan(1e-3);
  expect(Math.abs(c.lon - 151.2)).toBeLessThan(1e-3);
  expect(Math.abs(c.rangeKm - 40)).toBeLessThan(0.05);
});

test("unusable input gives null, never a guess", () => {
  expect(boxFromCenter(NaN, 0, 10)).toBeNull();
  expect(boxFromCenter(85, 0, 10)).toBeNull();
  expect(boxFromCenter(0, 0, 0.5)).toBeNull();
  expect(boxFromCenter(0, 0, 500)).toBeNull();
  expect(boxFromCenter(0, 179.99, 50)).toBeNull();       // would cross the antimeridian
});
