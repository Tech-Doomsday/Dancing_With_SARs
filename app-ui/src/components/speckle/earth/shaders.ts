/**
 * Speckle shaders.
 *
 * Earth — NASA Blue Marble, graded like cinema: brighter day side with a
 * soft filmic shoulder, warm city lights on the night side, a tight ocean
 * glint, a whisper of scattering at the day-side limb and real geometric
 * relief (the topography map displaces the sphere and perturbs the normal,
 * so mountains shade the terminator).
 *
 * The landscape chapters (IV–VI) live on as flowing 2D sections in the
 * document — the stage carries only the Earth and the cloud veil that
 * hands the story over to them.
 */

/* ------------------------------------------------------------------ */
/* Earth                                                               */
/* ------------------------------------------------------------------ */

export const earthVertex = /* glsl */ `
uniform sampler2D topoMap;
uniform float dispAmt;      // geometric relief, world units
uniform float texelSize;    // 1 / topoMap width

varying vec2 vUv;
varying vec3 vWorldNormal;
varying vec3 vWorldPos;

float topo(vec2 uv) { return texture2D(topoMap, uv).r; }

void main() {
  vUv = uv;

  // --- real relief: displace along the normal
  float h = topo(uv);
  vec3 displaced = position + normal * (h - 0.5) * dispAmt;

  // --- recompute the normal from the height gradient (tangent space)
  vec2 tx = vec2(texelSize, 0.0);
  vec2 ty = vec2(0.0, texelSize * 2.0);
  float hL = topo(uv - tx);
  float hR = topo(uv + tx);
  float hD = topo(uv - ty);
  float hU = topo(uv + ty);
  // spherical tangent frame from the undisplaced normal
  vec3 up = abs(normal.y) < 0.99 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);
  vec3 tangent = normalize(cross(up, normal));
  vec3 bitangent = normalize(cross(normal, tangent));
  vec3 grad = tangent * (hR - hL) + bitangent * (hU - hD);
  vec3 n = normalize(normal - grad * 1.6);

  vWorldNormal = normalize(mat3(modelMatrix) * n);
  vec4 wp = modelMatrix * vec4(displaced, 1.0);
  vWorldPos = wp.xyz;
  gl_Position = projectionMatrix * viewMatrix * wp;
}
`;

export const earthFragment = /* glsl */ `
uniform sampler2D dayMap;
uniform sampler2D nightMap;
uniform sampler2D waterMap;
uniform vec3 sunDir;        // world space, normalized
uniform float uFade;

varying vec2 vUv;
varying vec3 vWorldNormal;
varying vec3 vWorldPos;

void main() {
  vec3 n = normalize(vWorldNormal);
  vec3 viewDir = normalize(cameraPosition - vWorldPos);

  float ndl = dot(n, sunDir);
  // soft, slightly wide terminator
  float dayAmt = smoothstep(-0.12, 0.18, ndl);

  vec3 dayCol = texture2D(dayMap, vUv).rgb;
  vec3 nightCol = texture2D(nightMap, vUv).rgb;
  nightCol *= vec3(1.0, 0.76, 0.5) * 1.4;    // warm, living city lights
  nightCol += dayCol * 0.045;                // faint earthshine

  float diff = clamp(ndl, 0.0, 1.0);
  vec3 color = mix(nightCol, dayCol * (0.22 + 1.5 * diff), dayAmt);

  // --- ocean sun glint (water mask: white = water)
  float water = texture2D(waterMap, vUv).r;
  vec3 halfDir = normalize(sunDir + viewDir);
  float spec = pow(max(dot(n, halfDir), 0.0), 96.0) * water * dayAmt;
  color += vec3(0.95, 0.97, 1.0) * spec * 0.85;

  // --- dusk band: a thin warm stain right at the terminator
  float dusk = smoothstep(0.24, 0.0, abs(ndl)) * dayAmt;
  color += vec3(0.28, 0.13, 0.04) * dusk * 0.4;

  // --- atmosphere: in-scattering at the day-side limb. No halo, no glow —
  //     just enough air to feel like a planet breathing light.
  float fres = pow(1.0 - max(dot(n, viewDir), 0.0), 3.4);
  color += vec3(0.36, 0.5, 0.68) * fres * (0.12 + 0.3 * dayAmt);

  // --- cinematic grade: gentle saturation lift, then a bright filmic
  //     shoulder so the day side reads luminous instead of CG-flat
  float lum = dot(color, vec3(0.2126, 0.7152, 0.0722));
  color = mix(vec3(lum), color, 1.1);
  color = 1.0 - exp(-color * 1.5);
  color = pow(color, vec3(1.04));

  gl_FragColor = vec4(color, uFade);
}
`;
