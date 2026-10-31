/**
 * Speckle — cinematic landing page content, themes & integration map.
 *
 * All hazard-pipeline facts below are extracted verbatim from the
 * Dancing_With_SARs repository (engine/pipelines/…/manifest.yaml) so the
 * landing page never claims a module that does not exist.
 */

import { TL } from "./scroll";

export const REPO_URL = "https://github.com/Tech-Doomsday/Dancing_With_SARs";

/**
 * Entry point of the real application (the hazard console built with the
 * engine). When this landing page is merged into the repository, set
 * NEXT_PUBLIC_SPECKLE_APP_URL to the deployed app-ui entry (e.g. "/console").
 * Until then the repository itself is the honest destination.
 */
export const APP_URL =
  process.env.NEXT_PUBLIC_SPECKLE_APP_URL?.trim() || REPO_URL;

export const LINKS = {
  app: APP_URL,
  earthquakes: APP_URL,
  hurricanes: `${REPO_URL}/tree/main/engine/pipelines/flood_inundation`,
  erosion: `${REPO_URL}/tree/main/engine/pipelines/river_erosion`,
  architecture: `${REPO_URL}/blob/main/docs/ARCHITECTURE.md`,
  repo: REPO_URL,
  spaceapps: "https://www.spaceappschallenge.org/",
  nasaVisibleEarth: "https://visibleearth.nasa.gov/",
} as const;

/* ------------------------------------------------------------------ */
/* Chapter themes — each chapter grades the whole page                 */
/* ------------------------------------------------------------------ */

export interface ChapterTheme {
  /** accent for kickers, rails, ticks and focus rings */
  accent: string;
  /** deep background the 3D fog fades toward */
  bg: string;
  label: string;
}

/* One film, one grade — the whole site shares a single accent and a
   single deep-space dark, so every chapter reads as part of the same
   story instead of its own colour scheme. */
export const THEMES: Record<string, ChapterTheme> = {
  space: { accent: "#a9bfd6", bg: "#04070d", label: "A world seen from space" },
  move: { accent: "#a9bfd6", bg: "#04070d", label: "The planet begins to move" },
  descent: { accent: "#a9bfd6", bg: "#04070d", label: "Descending into the landscape" },
  quake: { accent: "#a9bfd6", bg: "#04070d", label: "When the ground shifts" },
  water: { accent: "#a9bfd6", bg: "#04070d", label: "When water reshapes the land" },
  river: { accent: "#a9bfd6", bg: "#04070d", label: "Where rivers redraw the land" },
  evidence: { accent: "#a9bfd6", bg: "#04070d", label: "From observation to evidence" },
  engine: { accent: "#a9bfd6", bg: "#04070d", label: "The engine" },
  innovation: { accent: "#a9bfd6", bg: "#04070d", label: "What makes Speckle different" },
  architecture: { accent: "#a9bfd6", bg: "#04070d", label: "How Speckle is built" },
  correlation: { accent: "#a9bfd6", bg: "#04070d", label: "From results to reasons" },
  impact: { accent: "#a9bfd6", bg: "#04070d", label: "What it makes possible" },
  home: { accent: "#a9bfd6", bg: "#04070d", label: "The view from home" },
  finale: { accent: "#a9bfd6", bg: "#04070d", label: "Enter Speckle" },
};

/** theme key → story progress ranges over the pinned Earth room (kept in sync with TL) */
export const THEME_TRACK: { key: string; start: number; end: number }[] = [
  { key: "space", start: 0, end: TL.ch2.start },
  { key: "move", start: TL.ch2.start, end: TL.ch3.start },
  { key: "descent", start: TL.ch3.start, end: 1.01 },
];

/* ------------------------------------------------------------------ */
/* Story copy                                                          */
/* ------------------------------------------------------------------ */

export const HERO = {
  eyebrow: "NASA Space Apps Challenge 2026 · Dancing with the SARs",
  title: "Speckle",
  subtitle: "Earth is always changing.",
  body: "Discover the changes shaping our planet, observed from space.",
  cue: "Scroll to begin",
} as const;

export const CHAPTER_2 = {
  kicker: "Chapter II · The planet begins to move",
  heading: "A changing planet leaves evidence.",
  body: "Spin the globe and the evidence is already there: coasts drift, ground lifts, rivers wander. Satellite radar can read that record even through cloud and darkness.",
} as const;

export const CHAPTER_3 = {
  kicker: "Chapter III · Descending into the landscape",
  heading: "Every landscape tells a different story.",
  body: "Leave orbit and fall toward the surface, from a whole planet down to a single ridge, a single shoreline, a single river bend.",
} as const;

export const CHAPTER_4_INTRO = {
  kicker: "Chapter IV · The changing systems",
  heading: "Discover the Earth’s changing systems.",
  body: "The land below is what the engine actually looks at. Keep scrolling, and the terrain itself carries you from one system to the next.",
  cue: "Continue the descent",
} as const;

/* ------------------------------------------------------------------ */
/* Sectors (status reflects the actual engine/pipelines state)         */
/* ------------------------------------------------------------------ */

export type SectorStatus = "live" | "planned";

export interface Sector {
  id: string;
  index: string;
  kicker: string;
  heading: string;
  body: string;
  image: string;
  imageAlt: string;
  status: SectorStatus;
  cta: { label: string; href: string };
  meta: string[];
  align: "left" | "right";
  theme: ChapterTheme;
  /** heightmap derivation: invert luminance, displacement strength */
  height: { invert: boolean; strength: number; bias: number };
}

export const SECTORS: Sector[] = [
  {
    id: "system-earthquakes",
    index: "01",
    kicker: "Ground deformation",
    heading: "When the ground shifts.",
    body: "Earthquakes rearrange the surface in seconds. Radar measures the displacement between satellite passes. Then physics checks whether the answer holds together.",
    image: "/landscapes/earthquake.webp",
    imageAlt:
      "Aerial view of a rugged arid landscape cut by a visible geological fault line.",
    status: "live",
    cta: { label: "Explore earthquakes", href: LINKS.earthquakes },
    meta: [
      "earthquake_coseismic · 3.01",
      "Physics: Okada85 + consistency screen",
      "Status: implemented",
    ],
    align: "left",
    theme: THEMES.quake,
    height: { invert: false, strength: 1.0, bias: 0.0 },
  },
  {
    id: "system-hurricanes",
    index: "02",
    kicker: "Water & storm",
    heading: "When water reshapes the land.",
    body: "Storms rewrite coastlines and floodplains. Mapping water where it shouldn’t be is a different problem from measuring ground movement, and the engine never confuses the two.",
    image: "/landscapes/delta.webp",
    imageAlt:
      "Aerial view of a river delta meeting the ocean, dark landmasses against bright reflective water.",
    status: "planned",
    cta: { label: "Explore hurricanes", href: LINKS.hurricanes },
    meta: [
      "flood_inundation · 3.07 · in development",
      "tropical_cyclone · 3.13 · in development",
      "Event feeds: ComCat · IBTrACS, live",
    ],
    align: "right",
    theme: THEMES.water,
    height: { invert: true, strength: 0.72, bias: -0.04 },
  },
  {
    id: "system-erosion",
    index: "03",
    kicker: "Sediment & shifting channels",
    heading: "Where rivers redraw the land.",
    body: "Rivers erode one bank and build up another, decade after decade. Geometry and statistics trace those changes along the whole corridor.",
    image: "/landscapes/river_alt.webp",
    imageAlt:
      "Aerial view of a winding river meandering through a dense forest landscape.",
    status: "planned",
    cta: { label: "Explore river erosion", href: LINKS.erosion },
    meta: [
      "river_erosion · 3.10 · in development",
      "Geometry + statistics",
      "River corridors: S11 buffer, water occurrence",
    ],
    align: "left",
    theme: THEMES.river,
    height: { invert: false, strength: 0.85, bias: 0.05 },
  },
];

/* ------------------------------------------------------------------ */
/* The engine watchlist — real pipeline catalog from the repository     */
/* ------------------------------------------------------------------ */

export interface PipelineRow {
  code: string;
  name: string;
  live: boolean;
}

export const PIPELINES: PipelineRow[] = [
  { code: "3.01", name: "earthquake_coseismic", live: true },
  { code: "3.02", name: "land_subsidence", live: false },
  { code: "3.03", name: "volcanic_deformation", live: false },
  { code: "3.04", name: "landslide", live: false },
  { code: "3.05", name: "glacier_ice_flow", live: false },
  { code: "3.06", name: "permafrost_thaw", live: false },
  { code: "3.07", name: "flood_inundation", live: false },
  { code: "3.08", name: "deforestation", live: false },
  { code: "3.09", name: "wildfire_burn_scar", live: false },
  { code: "3.10", name: "river_erosion", live: false },
  { code: "3.11", name: "coastal_erosion", live: false },
  { code: "3.12", name: "urban_damage", live: false },
  { code: "3.13", name: "tropical_cyclone", live: false },
  { code: "3.14", name: "tsunami_inundation", live: false },
  { code: "3.15", name: "oil_spill", live: false },
  { code: "3.16", name: "iceberg_calving_drift", live: false },
  { code: "3.17", name: "tsunami_ocean_evidence", live: true },
];

/* ------------------------------------------------------------------ */
/* Evidence — the engine's real verdict vocabulary (engine/verdict)     */
/* ------------------------------------------------------------------ */

export const VERDICT_TIERS: { label: string; note: string; tone: string }[] = [
  { label: "supported by evidence", note: "the signal survives its consistency screens", tone: "ok" },
  { label: "candidate / possible", note: "worth a closer look, not yet conclusive", tone: "mid" },
  { label: "not supported by evidence", note: "the observations argue against it", tone: "off" },
  { label: "not enough evidence", note: "the archive is genuinely young", tone: "dim" },
  { label: "did not apply here", note: "the pipeline itself said “not for this tile”", tone: "dim" },
  { label: "not modelled yet", note: "honestly absent, not silently skipped", tone: "dim" },
];

export const EVIDENCE = {
  kicker: "Chapter V · From observation to evidence",
  heading: "Observe. Compare. Investigate.",
  body: "A satellite image is an observation, not a conclusion. An algorithm produces an estimate. Interpretation needs evidence, and it needs the uncertainty stated plainly.",
  stages: [
    {
      n: "01",
      title: "Observe",
      body: "Satellites watch the same ground again and again. Radar adds what cameras can’t get through: cloud, darkness, and motion at millimetre scale.",
    },
    {
      n: "02",
      title: "Compare",
      body: "Algorithms compare observations across time (coherence, backscatter, phase) and turn the difference into an estimate, with the uncertainty attached. Drag the divider and the terrain behind it answers.",
    },
    {
      n: "03",
      title: "Investigate",
      body: "A human reads the evidence. Every verdict names its tier and no number it hasn’t earned: there are no calibrated probabilities until labelled histories exist. That part is deliberate.",
    },
  ],
  compareLabels: {
    left: "Optical · what sunlight shows",
    right: "SAR · what radar shows",
    hint: "Drag to compare",
  },
  engineKicker: "The engine",
  engineHeading: "Seventeen detectors. One verdict.",
  engineBody:
    "The engine runs every detector that could apply to a scene: sixteen hazard types, seventeen independent pipelines. No pipeline reads another’s output, and every answer carries its own evidence.",
  footnote:
    "Scenes above are illustrative examples of observation types, not measurement results from a specific event.",
} as const;

/* ------------------------------------------------------------------ */
/* Chapter VI — Innovation: what actually makes Speckle different        */
/* (facts from docs/ARCHITECTURE.md §1–§2)                             */
/* ------------------------------------------------------------------ */

export const INNOVATION = {
  kicker: "Chapter VI · What makes Speckle different",
  heading: "Three rules, written into the code.",
  body: "Most platforms show you a picture and let you guess. We built Speckle the other way around: three rules, enforced by the architecture itself rather than promised in a readme. Scroll through one radar pass to read them.",
  cue: "Follow the pass",
  beats: [
    {
      rule: "Rule 01",
      title: "Independence is structural.",
      body: "Sixteen hazards, seventeen pipelines, and no pipeline is allowed to read another’s output. That isn’t a convention; it’s enforced by where the code is allowed to live. Shared physics and statistics sit in one library that every pipeline can see equally, so a tile that triggers three hazards gets three separate answers, each derived on its own.",
      align: "left" as const,
    },
    {
      rule: "Rule 02",
      title: "The evidence travels with the verdict.",
      body: "Every answer reports what it found, how confident it is, and exactly which data and corrections it used. Hazards that cause each other (quake → landslide → tsunami → urban damage) are tied together afterwards by the verdict stage, never fused inside a single model where the origins of the evidence would get lost.",
      align: "right" as const,
    },
    {
      rule: "Rule 03",
      title: "Honest about not knowing.",
      body: "“Not applicable” means something different from “no event.” No correction is ever applied silently. And until enough labelled history exists to earn calibration, pipelines report status tiers and within-tier scores, never a probability they haven’t earned.",
      align: "left" as const,
    },
  ],
  ossKicker: "Built on proven open science",
  ossHeading: "Where it’s mature, we don’t reinvent it.",
  ossBody:
    "We spend our own engineering on orchestration, fetch and integration, not on rewriting physics that already works. The building blocks are public, peer-reviewed and battle-tested.",
  ossChips: [
    "Okada85",
    "MintPy",
    "autoRIFT",
    "DSWX-SAR",
    "ARIA DPM",
    "RivGraph",
    "CoastSat",
    "pyTMD",
  ],
  ossLink: { label: "See how each pipeline is built", href: LINKS.architecture },
} as const;

/* ------------------------------------------------------------------ */
/* Chapter VII — Architecture: the engine, easy to read                 */
/* (layers & flow from docs/ARCHITECTURE.md §3–§4)                     */
/* ------------------------------------------------------------------ */

export interface ArchLayer {
  no: string;
  id: string;
  title: string;
  tagline: string;
  body: string;
  chips: string[];
}

export const ARCHITECTURE = {
  kicker: "Chapter VII · How Speckle is built",
  heading: "One machine, in five stages.",
  body: "The engine reads in one direction: raw satellite data goes in at the bottom, answers come out at the top. Five stages, each doing exactly one job. Scroll to take it apart.",
  cue: "Take it apart",
  layers: <ArchLayer[]>[
    {
      no: "01",
      id: "retrieval",
      title: "Retrieval",
      tagline: "Decide what to fetch, then get it",
      body: "Pollers watch earthquake feeds, fire detections and storm tracks around the clock. When something moves, a dedicated client for that source fetches exactly the data needed, keeps it cached on disk, and complains loudly when it fails.",
      chips: ["event pollers", "per-source clients", "TTL cache"],
    },
    {
      no: "02",
      id: "bundle",
      title: "The SceneBundle",
      tagline: "One read-only object",
      body: "All shared preprocessing runs once per scene, then freezes into a single read-only bundle. Every pipeline reads that same immutable object, so no detector can ever see different data.",
      chips: ["P01–P13", "read-only", "provenance attached"],
    },
    {
      no: "03",
      id: "pipelines",
      title: "The pipelines",
      tagline: "Seventeen watchers, zero gossip",
      body: "Each of the seventeen hazard detectors lives in its own folder and runs in parallel; none of them may read another’s output. Shared physics sits in a common library, equally visible to all.",
      chips: ["independent", "process pool", "manifest-first"],
    },
    {
      no: "04",
      id: "verdict",
      title: "The verdict",
      tagline: "Collect · calibrate · link · select · persist",
      body: "Every return is collected, calibrated where possible, and linked into causal chains like storm → flood → erosion. Conflicting explanations are settled by evidence before anything gets saved.",
      chips: ["causal links", "no silent fusion", "status tiers"],
    },
    {
      no: "05",
      id: "serving",
      title: "Serving",
      tagline: "Answers, out in the open",
      body: "Tiles, events and evidence ship over a clean public API. The console is generated from each pipeline’s own manifest, so the interface can never claim more than the pipeline actually said.",
      chips: ["/tiles", "/events", "/scanner/status"],
    },
  ],
  flowCaption: "DATA FLOWS · RETRIEVAL → SERVING",
  sideKicker: "The quiet machinery",
  sideNotes: [
    {
      title: "engine/lib",
      body: "Shared physics and statistics (Okada85, flood detection, offset tracking): the only code pipelines are allowed to cross-import.",
    },
    {
      title: "The registry",
      body: "Scans the pipeline folders, reads every manifest, and runs whatever applies, in a parallel process pool.",
    },
    {
      title: "Validation",
      body: "Labels, fixtures and regression tests: the history that will one day earn calibrated probabilities.",
    },
  ],
  cta: { label: "Read the full architecture document", href: LINKS.architecture },
} as const;

/* ------------------------------------------------------------------ */
/* Chapter VIII — Correlations: from results to reasons                */
/* ------------------------------------------------------------------ */

export interface CorrPipeline {
  code: string;
  name: string;
  live?: boolean;
}

export interface Correlation {
  id: string;
  index: string;
  domain: string;
  title: string;
  chain: { label: string; text: string }[];
  body: string;
  note?: string;
  image: string;
  imageAlt: string;
  pipelines: CorrPipeline[];
  accent: string;
}

export const CORRELATIONS = {
  kicker: "Chapter VIII · From results to reasons",
  heading: "Every result is the start of a chain.",
  body: "A measurement is not a conclusion. An event leaves a signature in the data, and the signature implies consequences on the ground. Following that chain from end to end is the science Speckle is built for.",
  chainLabels: ["Event", "Signature", "Consequence"],
  chains: <Correlation[]>[
    {
      id: "corr-quake",
      index: "C·01",
      domain: "Ground deformation",
      title: "When the ground breaks, it leaves craters.",
      chain: [
        { label: "Event", text: "A strong earthquake strikes wet, sandy ground" },
        { label: "Signature", text: "Surface rupture, liquefaction craters & sand boils" },
        { label: "Consequence", text: "Displacement measurable from orbit, if physics agrees" },
      ],
      body: "Coseismic slip cuts a rupture across the surface, and violent shaking can liquefy waterlogged sand until it erupts in craters and sand boils. Radar measures the ground’s movement between passes at millimetre scale, then asks Okada85 elastic physics whether the answer holds together. Both nodal planes are tested, and the better fit keeps the verdict.",
      note: "The verdict stage links the chain onward: quake → landslide → tsunami → urban damage.",
      image: "/landscapes/liquefaction.webp",
      imageAlt:
        "Aerial view of farmland crossed by a fresh geological rupture dotted with pale liquefaction craters.",
      pipelines: [
        { code: "3.01", name: "earthquake_coseismic", live: true },
        { code: "3.04", name: "landslide" },
        { code: "3.17", name: "tsunami_ocean_evidence", live: true },
      ],
      accent: "#a9bfd6",
    },
    {
      id: "corr-salinity",
      index: "C·02",
      domain: "Storm & coast",
      title: "The sea comes inland; the salt stays.",
      chain: [
        { label: "Event", text: "A cyclone pushes the ocean over the coast" },
        { label: "Signature", text: "Water where it should never be, then salt in the soil" },
        { label: "Consequence", text: "Coastal farmland marked for seasons after the storm" },
      ],
      body: "Storm surge drowns coastal land in seawater. The flood recedes but the salt remains, and long after the cyclone itself is forgotten, the fields remember. The verdict stage links the storm to the flood it caused, and the coast keeps watching what the water left behind.",
      image: "/landscapes/salinity.webp",
      imageAlt:
        "Aerial view of coastal rice paddies with pale salt-crusted barren patches between green fields.",
      pipelines: [
        { code: "3.13", name: "tropical_cyclone" },
        { code: "3.07", name: "flood_inundation" },
        { code: "3.11", name: "coastal_erosion" },
      ],
      accent: "#a9bfd6",
    },
    {
      id: "corr-forest",
      index: "C·03",
      domain: "Forest & slopes",
      title: "Where the forest falls, the marking is permanent.",
      chain: [
        { label: "Event", text: "Clearing, by axe, by fire or by wind" },
        { label: "Signature", text: "A persistent drop in cross-polarised backscatter" },
        { label: "Consequence", text: "Slopes uncovered: erosion and landslide exposure" },
      ],
      body: "Radar’s cross-polarised signal falls when canopy disappears. That sequential, persistent change marks each clearing patch, tells fire from felling from windthrow, and holds the history of every hectare. And once the canopy is gone, the slopes beneath it are exposed: the same engine that watched the forest fall watches what the rain does next.",
      image: "/landscapes/deforestation.webp",
      imageAlt:
        "Aerial view of rainforest canopy with sharp-edged clearings and fishbone logging roads.",
      pipelines: [
        { code: "3.08", name: "deforestation" },
        { code: "3.04", name: "landslide" },
      ],
      accent: "#a9bfd6",
    },
    {
      id: "corr-urban",
      index: "C·04",
      domain: "Urbanization",
      title: "As the city grows, the hazard grows with it.",
      chain: [
        { label: "Event", text: "Expansion seals ground and crowds structures" },
        { label: "Signature", text: "Changed radar returns, and coherence that drops where damage concentrates" },
        { label: "Consequence", text: "Amplified flooding, concentrated event damage" },
      ],
      body: "Ground that used to soak up rain now sheds it, and buildings crowded together change how radar echoes return. When an earthquake or a storm hits a grown city, coherence collapses exactly where damage concentrates: the growth that raised the exposure also wrote it into the signal. Urbanization and natural events are not separate stories; they are the same story read twice.",
      image: "/landscapes/urban.webp",
      imageAlt:
        "Aerial view of a dense South Asian megacity at dusk meeting open floodplain water.",
      pipelines: [
        { code: "3.12", name: "urban_damage" },
        { code: "3.07", name: "flood_inundation" },
      ],
      accent: "#a9bfd6",
    },
    {
      id: "corr-oil",
      index: "C·05",
      domain: "Ocean & slicks",
      title: "Oil reads as darkness on a bright sea.",
      chain: [
        { label: "Event", text: "A spill spreads across open water" },
        { label: "Signature", text: "Damped capillary waves: a dark patch in radar" },
        { label: "Consequence", text: "A coastline downstream on the current’s schedule" },
      ],
      body: "Oil stills the tiny capillary waves that make the sea scatter radar bright, so a spill reads as darkness on a bright sea. Adaptive local thresholds find the dark spots, and a classifier separates oil from wind shadows and biogenic look-alikes. What matters most is not the patch itself but the coast downstream.",
      image: "/landscapes/oilspill.webp",
      imageAlt: "Aerial view of a dark oil slick trailing across grey ocean water.",
      pipelines: [
        { code: "3.15", name: "oil_spill" },
        { code: "3.11", name: "coastal_erosion" },
      ],
      accent: "#a9bfd6",
    },
    {
      id: "corr-river",
      index: "C·06",
      domain: "Rivers & soil",
      title: "The river’s hidden work: bends no eye can see.",
      chain: [
        { label: "Event", text: "A meander deepens; the channel begins to wander" },
        { label: "Signature", text: "Outer bank retreats, inner bar deposits, epoch after epoch" },
        { label: "Consequence", text: "Unseen soil structure undercut until the bank fails" },
      ],
      body: "The outer bend erodes while the inner deposits, and the channel wanders across the plain: undercutting banks, exposing soil structure no ground survey can see, moving metres in a single monsoon. Epoch by epoch, water masks trace the retreat transect by transect along the corridor, until the river’s slow work becomes a map you can act on.",
      image: "/landscapes/river.webp",
      imageAlt: "Aerial view of a winding river meandering through forested land.",
      pipelines: [
        { code: "3.10", name: "river_erosion" },
        { code: "3.07", name: "flood_inundation" },
      ],
      accent: "#a9bfd6",
    },
  ],
} as const;

/* ------------------------------------------------------------------ */
/* Chapter IX — Impact: what Speckle is built to make possible           */
/* ------------------------------------------------------------------ */

export const IMPACT = {
  kicker: "Chapter IX · What Speckle is built to make possible",
  heading: "Answers people can act on.",
  body: "A verdict is only worth having if someone can do something with it. Three audiences shaped every design decision in the engine.",
  rows: [
    {
      n: "01",
      who: "For responders",
      title: "Evidence that arrives with its provenance.",
      body: "When the ground breaks or the storm makes landfall, a disaster team needs a map it can defend. Every Speckle verdict carries what was measured, how confident it is, and what to check next. There is no black box between the disaster and the decision.",
    },
    {
      n: "02",
      who: "For scientists",
      title: "Sixteen detectors, no hidden fusion.",
      body: "Every verdict separates back into its evidence, its code and its corrections, which makes it something you can argue with, not just believe. Each pipeline’s status is public, including the unflattering ones.",
    },
    {
      n: "03",
      who: "For communities & planners",
      title: "The slow changes, watched patiently.",
      body: "Subsiding ground, retreating banks, thinning forest, growing floodplains. Change that unfolds over years is the hardest to see and the most important to plan around, so Speckle is built to watch it year after year and report it with its uncertainty attached.",
    },
  ],
  lifecycleKicker: "The life of an answer",
  lifecycle: [
    { label: "Event", note: "an earthquake lands in ComCat · a cyclone forms in IBTrACS" },
    { label: "Trigger", note: "rules wake the tiles that matter" },
    { label: "Scan", note: "every applicable pipeline runs, in parallel" },
    { label: "Verdict", note: "status tiers + evidence, linked into chains" },
    { label: "Response", note: "a map that can defend itself" },
  ],
  footnote:
    "Speckle is young. These are the goals it is engineered toward, and we state them with the same care as its verdicts.",
} as const;

/* ------------------------------------------------------------------ */
/* Chapter X — Home: why this matters in Bangladesh                    */
/* ------------------------------------------------------------------ */

export interface HomeWatch {
  region: string;
  tag: string;
  body: string;
  pipelines: CorrPipeline[];
}

export const HOME = {
  kicker: "Chapter X · The view from home",
  heading: "Built in Bangladesh, for the delta radar was made for.",
  body: "For much of the year, monsoon cloud hides this country from every optical satellite in orbit. L-band radar does not care: it measures through cloud and darkness, at millimetre scale. Home is the hardest proving ground we could have picked.",
  watchKicker: "The home watchlist",
  watchHeading: "What we’re building it to watch.",
  watches: <HomeWatch[]>[
    {
      region: "The delta & its chars",
      tag: "JAMUNA · BRAHMAPUTRA",
      body: "The Jamuna braids and wanders, and its chars (river islands) are born and erased within years. Bank erosion moves land, livelihoods and people, monsoon after monsoon, along bends no survey can keep up with.",
      pipelines: [
        { code: "3.10", name: "river_erosion" },
        { code: "3.07", name: "flood_inundation" },
      ],
    },
    {
      region: "The coast & the Sundarbans",
      tag: "BAY OF BENGAL",
      body: "Cyclones push the sea over coastal districts and the surge leaves salt in the fields, while the mangrove line is the last defence. What the storm floods, the soil remembers long after the sky clears.",
      pipelines: [
        { code: "3.13", name: "tropical_cyclone" },
        { code: "3.11", name: "coastal_erosion" },
        { code: "3.08", name: "deforestation" },
      ],
    },
    {
      region: "The floodplains",
      tag: "PADMA · MEGHNA BASIN",
      body: "Flood is not an event here, it is a season. The plains breathe with the rivers, and where a delta compacts, the ground itself slowly sinks beneath the water line.",
      pipelines: [
        { code: "3.07", name: "flood_inundation" },
        { code: "3.02", name: "land_subsidence" },
      ],
    },
    {
      region: "The growing cities",
      tag: "DHAKA & BEYOND",
      body: "Dhaka is one of the fastest-growing megacities on Earth. That expansion seals floodplain, concentrates shaking, and rewrites every hazard map around it. The 1885 Bengal earthquake is a reminder that the fault lines beneath it are real.",
      pipelines: [
        { code: "3.12", name: "urban_damage" },
        { code: "3.01", name: "earthquake_coseismic", live: true },
      ],
    },
  ],
  note: "These are not claims of results, only the readiness to watch. Every pipeline above is on the public roadmap, with its status in the open.",
  cta: { label: "Follow the build", href: LINKS.repo },
} as const;

export const FINALE = {
  kicker: "Chapter XI · Enter Speckle",
  heading: "The story is written on Earth.",
  body: "Explore the observations. Investigate the changes. Understand what the evidence can tell us.",
  primary: { label: "Start exploring", href: LINKS.app },
  secondary: { label: "How Speckle works", href: LINKS.architecture },
} as const;

export const FOOTER = {
  blurb:
    "An Earth-observation story built for NASA Space Apps Challenge 2026, Dancing with the SARs.",
  disclaimer:
    "A Space Apps challenge project. Not affiliated with or endorsed by NASA.",
  credits:
    "Earth imagery: NASA Visible Earth (Blue Marble and city lights). Landscape scenes are editorial photography.",
} as const;
