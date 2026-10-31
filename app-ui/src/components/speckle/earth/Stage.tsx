"use client";

/**
 * The Speckle stage — one WebGL canvas for the Earth chapters.
 *
 * Chapters I–III live here as a pinned scene: a still, photorealistic
 * globe that only moves when the visitor scrolls (rotation is a story
 * event, not an idle animation), real relief from the topography map, and
 * a camera that rides ONE keyframe track from the first frame to the last.
 * The story ends diving into a cloud veil that the DOM closes to black —
 * the handoff point where the flowing 2D chapters scroll up over the
 * darkened canvas.
 *
 * Every value is derived from `storyState.smooth`: if the visitor stops
 * scrolling, the world is perfectly still.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { Canvas, useFrame } from "@react-three/fiber";
import {
  storyState,
  smoothstep,
  mix,
  track,
  track1,
  setLoadProgress,
} from "../scroll";
import { EARTH_ROTY } from "./choreo";
import { earthVertex, earthFragment } from "./shaders";

/* ------------------------------------------------------------------ */
/* the ONE camera track — hero to the cloud dive, no cuts               */
/* ------------------------------------------------------------------ */

const CAM_POS: { p: number; v: number[] }[] = [
  { p: 0.0, v: [0.05, 0.46, 6.3] }, // hero — big Earth, right of frame
  { p: 0.24, v: [-0.02, 0.36, 5.95] },
  { p: 0.4, v: [0.12, 0.28, 5.55] }, // ch2 — Earth sweeps left
  { p: 0.57, v: [0.3, 0.2, 4.7] }, // ch3 — recentre, begin descent
  { p: 0.71, v: [0.5, 0.12, 3.6] },
  { p: 0.87, v: [0.55, 0.04, 2.8] }, // skim the limb, clouds ahead
  { p: 1.0, v: [0.52, -0.06, 2.55] }, // dive into the veil
];

const CAM_LOOK: { p: number; v: number[] }[] = [
  { p: 0.0, v: [-0.85, 0.16, 0.0] }, // Earth holds the right of frame
  { p: 0.24, v: [-0.5, 0.1, 0.0] },
  { p: 0.4, v: [0.7, 0.02, 0.0] }, // Earth holds the left of frame
  { p: 0.57, v: [0.3, 0.0, 0.0] },
  { p: 0.71, v: [0.5, -0.1, 0.0] },
  { p: 0.87, v: [0.5, -0.26, 0.0] },
  { p: 1.0, v: [0.5, -0.3, 0.0] },
];

const SUN_DIR: { p: number; v: number[] }[] = [
  { p: 0.0, v: [-0.72, 0.3, 0.55] },
  { p: 0.57, v: [-0.5, 0.26, 0.62] },
  { p: 0.95, v: [0.1, 0.18, 0.65] },
  { p: 1.0, v: [0.1, 0.18, 0.65] },
];

/* ------------------------------------------------------------------ */
/* stars                                                               */
/* ------------------------------------------------------------------ */

function Stars({
  count,
  matRef,
}: {
  count: number;
  matRef: React.RefObject<THREE.PointsMaterial | null>;
}) {
  const geo = useMemo(() => {
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const c = new THREE.Color();
    for (let i = 0; i < count; i++) {
      const u = Math.random();
      const v = Math.random();
      const theta = 2 * Math.PI * u;
      const phi = Math.acos(2 * v - 1);
      const r = 46 + Math.random() * 34;
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.cos(phi);
      positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
      const b = 0.35 + Math.pow(Math.random(), 2.4) * 0.65;
      c.setRGB(b * 0.92, b * 0.96, b);
      colors[i * 3] = c.r;
      colors[i * 3 + 1] = c.g;
      colors[i * 3 + 2] = c.b;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    return g;
  }, [count]);

  useEffect(() => {
    return () => geo.dispose();
  }, [geo]);

  return (
    <points geometry={geo} frustumCulled={false} renderOrder={-2}>
      <pointsMaterial
        ref={matRef}
        size={0.16}
        sizeAttenuation
        vertexColors
        transparent
        opacity={0.9}
        depthWrite={false}
      />
    </points>
  );
}

/* ------------------------------------------------------------------ */
/* the Earth                                                           */
/* ------------------------------------------------------------------ */

function EarthGlobe({
  textures,
  matRef,
  cloudsRef,
  groupRef,
  lightRef,
  lowPower,
}: {
  textures: {
    day: THREE.Texture;
    night: THREE.Texture;
    topo: THREE.Texture;
    water: THREE.Texture;
    clouds: THREE.Texture;
  };
  matRef: React.RefObject<THREE.ShaderMaterial | null>;
  cloudsRef: React.RefObject<THREE.Mesh | null>;
  groupRef: React.RefObject<THREE.Group | null>;
  lightRef: React.RefObject<THREE.DirectionalLight | null>;
  lowPower: boolean;
}) {
  const uniforms = useMemo(
    () => ({
      dayMap: { value: textures.day },
      nightMap: { value: textures.night },
      topoMap: { value: textures.topo },
      waterMap: { value: textures.water },
      sunDir: { value: new THREE.Vector3(-0.72, 0.3, 0.55).normalize() },
      dispAmt: { value: 0.062 },
      texelSize: { value: 1 / 2048 },
      uFade: { value: 1 },
    }),
    [textures],
  );

  const seg = lowPower ? [72, 52] : [128, 88];

  return (
    <>
      <directionalLight ref={lightRef} intensity={2.0} />
      <group ref={groupRef} rotation={[0, 0, -0.072]}>
        <mesh>
          <sphereGeometry args={[2, seg[0], seg[1]]} />
          <shaderMaterial
            ref={matRef}
            vertexShader={earthVertex}
            fragmentShader={earthFragment}
            uniforms={uniforms}
            transparent
          />
        </mesh>
        <mesh ref={cloudsRef} scale={1.006}>
          <sphereGeometry args={[2, 48, 32]} />
          <meshLambertMaterial
            map={textures.clouds}
            transparent
            opacity={0.42}
            depthWrite={false}
          />
        </mesh>
      </group>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* the director — every frame derives the world from storyState.smooth  */
/* ------------------------------------------------------------------ */

function Director({
  earthTextures,
  onFirstFrame,
  lowPower,
}: {
  earthTextures: Parameters<typeof EarthGlobe>[0]["textures"];
  onFirstFrame: () => void;
  lowPower: boolean;
}) {
  const earthMat = useRef<THREE.ShaderMaterial>(null);
  const cloudsMesh = useRef<THREE.Mesh>(null);
  const earthGroup = useRef<THREE.Group>(null);
  const sunLight = useRef<THREE.DirectionalLight>(null);
  const starsMat = useRef<THREE.PointsMaterial>(null);

  const tmp = useMemo(
    () => ({
      pos: new THREE.Vector3(),
      look: new THREE.Vector3(),
      sun: new THREE.Vector3(),
    }),
    [],
  );

  const dbg = useRef(0);

  useEffect(() => {
    const raf = requestAnimationFrame(onFirstFrame);
    return () => cancelAnimationFrame(raf);
  }, [onFirstFrame]);

  useFrame((state) => {
    try {
    const s = storyState;
    const p = s.smooth;

    /* temporary diagnostics — exposed for verification tooling */
    if ((dbg.current++ & 63) === 0) {
      (window as unknown as Record<string, unknown>).__stageDebug = {
        p,
        calls: state.gl.info.render.calls,
        triangles: state.gl.info.render.triangles,
        sceneChildren: state.scene.children.length,
        cam: state.camera.position.toArray(),
        priority: state.internal.priority,
        frameloop: state.frameloop,
        canvasSize: [state.size.width, state.size.height],
        earthVisible: earthGroup.current?.visible ?? null,
        earthFade: earthMat.current?.uniforms.uFade.value ?? null,
      };
    }
    const camera = state.camera;
    const reduced = s.reduced;
    const mx = reduced ? 0 : s.mx;
    const my = reduced ? 0 : s.my;

    /* ---------------- Earth ---------------- */

    if (earthMat.current) {
      const u = earthMat.current.uniforms;
      // sun
      const sun = track(SUN_DIR, p);
      tmp.sun.set(sun[0], sun[1], sun[2]).normalize();
      u.sunDir.value.copy(tmp.sun);
      if (sunLight.current) {
        sunLight.current.position.copy(tmp.sun).multiplyScalar(12);
      }
      // the globe dissolves into the cloud veil at the end of the room
      u.uFade.value = 1 - smoothstep(0.833, 0.964, p);
    }

    if (earthGroup.current) {
      earthGroup.current.rotation.y = track1(EARTH_ROTY, p);
      if (cloudsMesh.current) {
        // clouds drift only relative to scroll — never on their own
        cloudsMesh.current.rotation.y =
          earthGroup.current.rotation.y * 0.082 + 0.03;
        const cm = cloudsMesh.current.material as THREE.MeshLambertMaterial;
        cm.opacity = 0.36 + 0.18 * smoothstep(0.48, 0.86, p);
      }
    }

    /* ---------------- camera — one continuous track ---------------- */
    const aspect = state.size.width / Math.max(state.size.height, 1);
    // portrait screens need to step back to keep the composition
    const aspectFix = aspect < 1 ? mix(1.45, 1.0, aspect) : 1;

    const pos = track(CAM_POS, p);
    const look = track(CAM_LOOK, p);

    // quiet pointer parallax — the world answers the hand
    tmp.pos.set(
      pos[0] + mx * 0.17,
      pos[1] - my * 0.09,
      pos[2] * aspectFix,
    );
    tmp.look.set(look[0], look[1], look[2] * aspectFix);

    camera.position.copy(tmp.pos);
    camera.lookAt(tmp.look);

    /* ---------------- stars ---------------- */
    if (starsMat.current) {
      // the sky goes quiet as the veil closes
      starsMat.current.opacity =
        clamp01((1 - smoothstep(0.86, 1.0, p)) * 0.9);
    }

    /* we own the render loop (priority 1) — auto-render is disabled, so
       the frame is drawn here, after every camera/uniform update */
    state.gl.render(state.scene, state.camera);
    } catch (err) {
      (window as unknown as Record<string, unknown>).__stageError = String(err);
    }
  }, 1);

  return (
    <>
      <Stars count={lowPower ? 900 : 1500} matRef={starsMat} />
      <EarthGlobe
        textures={earthTextures}
        matRef={earthMat}
        cloudsRef={cloudsMesh}
        groupRef={earthGroup}
        lightRef={sunLight}
        lowPower={lowPower}
      />
    </>
  );
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/* ------------------------------------------------------------------ */
/* asset loading                                                       */
/* ------------------------------------------------------------------ */

function useStageAssets(lowPower: boolean): {
  earth: Parameters<typeof EarthGlobe>[0]["textures"] | null;
} {
  const [earth, setEarth] = useState<Parameters<
    typeof EarthGlobe
  >[0]["textures"] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const TOTAL = 5;
    let done = 0;
    const tick = () => {
      done += 1;
      setLoadProgress(Math.min(0.94, done / TOTAL), done >= TOTAL);
    };

    const loadTex = (url: string, srgb: boolean, aniso = 8) =>
      new Promise<THREE.Texture>((resolve, reject) => {
        new THREE.TextureLoader().load(
          url,
          (t) => {
            if (srgb) t.colorSpace = THREE.SRGBColorSpace;
            t.anisotropy = aniso;
            tick();
            resolve(t);
          },
          undefined,
          reject,
        );
      });

    (async () => {
      try {
        const e = await Promise.all([
          loadTex("/textures/earth-blue-marble.jpg", true),
          loadTex("/textures/earth-night.jpg", true),
          loadTex("/textures/earth-topology.png", false, 4),
          loadTex("/textures/earth-water.png", false, 4),
          loadTex(
            lowPower ? "/textures/earth-clouds.png" : "/textures/earth-clouds-2k.webp",
            true,
          ),
        ]);
        if (cancelled) return;
        setLoadProgress(1, true);
        setEarth({
          day: e[0],
          night: e[1],
          topo: e[2],
          water: e[3],
          clouds: e[4],
        });
      } catch {
        if (!cancelled) setLoadProgress(1, true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [lowPower]);

  return { earth };
}

/* ------------------------------------------------------------------ */
/* exported canvas (dynamically imported — keeps three out of the      */
/* initial bundle)                                                     */
/* ------------------------------------------------------------------ */

export default function Stage({
  onReady,
  paused = false,
}: {
  onReady?: () => void;
  paused?: boolean;
}) {
  const lowPower = useMemo(() => {
    if (typeof window === "undefined") return false;
    return window.innerWidth < 768 || (navigator.hardwareConcurrency ?? 8) <= 4;
  }, []);

  const maxDpr = useMemo(() => {
    if (typeof window === "undefined") return 1.5;
    return Math.min(window.devicePixelRatio || 1, lowPower ? 1.5 : 1.75);
  }, [lowPower]);

  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    const onVis = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  const { earth } = useStageAssets(lowPower);
  const ready = earth !== null;
  const handleFirstFrame = useCallback(() => {
    if (ready) onReady?.();
  }, [ready, onReady]);

  const cameraConfig = useMemo(
    () => ({
      fov: 42,
      near: 0.1,
      far: 200,
      position: [0.05, 0.46, 6.3] as [number, number, number],
    }),
    [],
  );

  const glConfig = useMemo(
    () =>
      ({
        antialias: true,
        alpha: false,
        stencil: false,
        powerPreference: "high-performance",
      }) as const,
    [],
  );

  return (
    <Canvas
      flat
      dpr={[1, maxDpr]}
      camera={cameraConfig}
      gl={glConfig}
      frameloop={paused || hidden ? "never" : "always"}
    >
      <color attach="background" args={["#04070d"]} />
      {ready ? (
        <Director
          earthTextures={earth}
          onFirstFrame={handleFirstFrame}
          lowPower={lowPower}
        />
      ) : null}
    </Canvas>
  );
}
