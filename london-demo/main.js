// London walk/drive proof of concept.
// Loads raw Overpass API JSON (baked file, cached, or live), projects it to
// local metres, extrudes buildings, lays out roads/parks/water, and provides
// first-person walk and simple drive controls.

import * as THREE from 'three';
import { mergeGeometries } from './vendor/BufferGeometryUtils.js';

// ---------------------------------------------------------------------------
// Area of interest: Westminster — Trafalgar Square, Whitehall, Big Ben,
// Westminster Abbey, St James's Park, the Thames and the London Eye.
// Overpass bbox order is south, west, north, east.
const DEFAULT_BBOX = [51.4975, -0.1360, 51.5095, -0.1160];

// Spawn: Parliament Square, looking up Whitehall.
const SPAWN = { lat: 51.5006, lon: -0.1270, heading: 0.09 };

const OVERPASS_ENDPOINTS = [
  'https://overpass-api.de/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
];

const params = new URLSearchParams(location.search);
const bbox = (params.get('bbox') || '').split(',').map(Number).filter(n => !isNaN(n)).length === 4
  ? params.get('bbox').split(',').map(Number)
  : DEFAULT_BBOX;

const [S, W, N, E] = bbox;
const lat0 = (S + N) / 2, lon0 = (W + E) / 2;
const M_PER_DEG_LAT = 111132;
const M_PER_DEG_LON = 111320 * Math.cos(lat0 * Math.PI / 180);

// Project to a local plane: x = metres east, n = metres north.
function project(lat, lon) {
  return { x: (lon - lon0) * M_PER_DEG_LON, n: (lat - lat0) * M_PER_DEG_LAT };
}
function unproject(x, n) {
  return { lat: lat0 + n / M_PER_DEG_LAT, lon: lon0 + x / M_PER_DEG_LON };
}

const RECT = { // projected bbox, for closing clipped water polygons
  minX: project(S, W).x, maxX: project(S, E).x,
  minN: project(S, W).n, maxN: project(N, W).n,
};

// ---------------------------------------------------------------------------
// Data loading

const statusEl = document.getElementById('status');
const errorEl = document.getElementById('error');
function setStatus(msg) { statusEl.textContent = msg; }
function showError(msg) { errorEl.style.display = 'block'; errorEl.textContent = msg; }

const OVERPASS_QUERY = `
[out:json][timeout:120];
(
  way["building"](${S},${W},${N},${E});
  relation["building"](${S},${W},${N},${E});
);
out geom;
(
  way["highway"](${S},${W},${N},${E});
  way["leisure"~"^(park|garden|pitch|playground|recreation_ground)$"](${S},${W},${N},${E});
  relation["leisure"~"^(park|garden)$"](${S},${W},${N},${E});
  way["landuse"~"^(grass|meadow|forest|village_green|recreation_ground)$"](${S},${W},${N},${E});
  way["natural"~"^(water|wood|scrub)$"](${S},${W},${N},${E});
  relation["natural"="water"](${S},${W},${N},${E});
  way["waterway"="riverbank"](${S},${W},${N},${E});
);
out geom(${S},${W},${N},${E});
`;

async function fetchWithProgress(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${new URL(url).host}`);
  const reader = res.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    setStatus(`Downloading map data… ${(received / 1e6).toFixed(1)} MB`);
  }
  const buf = new Uint8Array(received);
  let off = 0;
  for (const c of chunks) { buf.set(c, off); off += c.length; }
  return JSON.parse(new TextDecoder().decode(buf));
}

async function loadData() {
  // 1. Explicit ?data= file (also used by the test fixture).
  const dataParam = params.get('data');
  if (dataParam) {
    setStatus(`Loading ${dataParam}…`);
    return fetchWithProgress(dataParam);
  }

  // 2. Pre-baked file from fetch_data.py.
  try {
    const baked = await fetch('data/london.json');
    if (baked.ok && (baked.headers.get('content-type') || '').includes('json')) {
      setStatus('Loading baked map data…');
      return await baked.json();
    }
  } catch (_) { /* fall through to live fetch */ }

  // 3. Live Overpass query (with per-bbox localStorage cache when it fits).
  const cacheKey = `osm:${bbox.join(',')}`;
  try {
    const cached = localStorage.getItem(cacheKey);
    if (cached) { setStatus('Loading cached map data…'); return JSON.parse(cached); }
  } catch (_) {}

  let lastErr;
  for (const endpoint of OVERPASS_ENDPOINTS) {
    try {
      setStatus(`Querying ${new URL(endpoint).host}…`);
      const data = await fetchWithProgress(endpoint, {
        method: 'POST',
        body: 'data=' + encodeURIComponent(OVERPASS_QUERY),
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      });
      try { localStorage.setItem(cacheKey, JSON.stringify(data)); } catch (_) {}
      return data;
    } catch (err) {
      lastErr = err;
    }
  }
  throw new Error(
    `Could not reach the Overpass API (${lastErr}).\n\n` +
    `If you are offline or behind a restrictive proxy, pre-bake the data once:\n\n` +
    `  python3 fetch_data.py\n\n` +
    `then reload — the demo will use data/london.json.`
  );
}

// ---------------------------------------------------------------------------
// Overpass element parsing

// A geometry array may contain nulls where Overpass clipped the way at the
// bbox; split it into contiguous chains of projected points.
function toChains(geometry) {
  const chains = [];
  let cur = [];
  for (const g of geometry || []) {
    if (g && typeof g.lat === 'number') {
      const p = project(g.lat, g.lon);
      cur.push([p.x, p.n]);
    } else if (cur.length) {
      if (cur.length >= 2) chains.push(cur);
      cur = [];
    }
  }
  if (cur.length >= 2) chains.push(cur);
  return chains;
}

const EPS = 0.01; // metres
const near = (a, b) => Math.abs(a[0] - b[0]) < EPS && Math.abs(a[1] - b[1]) < EPS;
const isClosed = c => c.length >= 4 && near(c[0], c[c.length - 1]);

// Stitch undirected open chains into longer chains/rings by matching endpoints.
function stitchChains(chains) {
  const open = [];
  const closed = [];
  for (const c of chains) (isClosed(c) ? closed : open).push(c.slice());

  let merged = true;
  while (merged) {
    merged = false;
    outer:
    for (let i = 0; i < open.length; i++) {
      for (let j = i + 1; j < open.length; j++) {
        const a = open[i], b = open[j];
        let joined = null;
        if (near(a[a.length - 1], b[0])) joined = a.concat(b.slice(1));
        else if (near(a[a.length - 1], b[b.length - 1])) joined = a.concat(b.slice(0, -1).reverse());
        else if (near(a[0], b[b.length - 1])) joined = b.concat(a.slice(1));
        else if (near(a[0], b[0])) joined = b.slice().reverse().concat(a.slice(1));
        if (joined) {
          open.splice(j, 1);
          open[i] = joined;
          if (isClosed(joined)) { closed.push(joined); open.splice(i, 1); }
          merged = true;
          break outer;
        }
      }
    }
  }
  return { closed, open };
}

function ringArea(ring) {
  let a = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  }
  return a / 2;
}

function pointInRing(pt, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 2; i < ring.length - 1; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > pt[1]) !== (yj > pt[1]) &&
        pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// --- Closing clipped polygons along the bbox border --------------------------
// Overpass clips water/park geometry at the bbox, leaving open chains whose
// endpoints lie on the border (e.g. the Thames entering and leaving the area).
// Reconnect them by walking along the border between chain endpoints. Winding
// of the source data is unknown, so try both walk directions and keep the one
// producing the smaller total area (the wrong direction yields the complement,
// covering nearly the whole bbox).

function perimeterT(p) { // position of a border point along the rect perimeter
  const { minX, maxX, minN, maxN } = RECT;
  const w = maxX - minX, h = maxN - minN;
  const dS = Math.abs(p[1] - minN), dE = Math.abs(p[0] - maxX);
  const dN = Math.abs(p[1] - maxN), dW = Math.abs(p[0] - minX);
  const m = Math.min(dS, dE, dN, dW);
  if (m === dS) return p[0] - minX;                    // south edge, west→east
  if (m === dE) return w + (p[1] - minN);              // east edge, south→north
  if (m === dN) return w + h + (maxX - p[0]);          // north edge, east→west
  return w + h + w + (maxN - p[1]);                    // west edge, north→south
}

function perimeterPoint(t) {
  const { minX, maxX, minN, maxN } = RECT;
  const w = maxX - minX, h = maxN - minN, P = 2 * (w + h);
  t = ((t % P) + P) % P;
  if (t < w) return [minX + t, minN];
  if (t < w + h) return [maxX, minN + (t - w)];
  if (t < w + h + w) return [maxX - (t - w - h), maxN];
  return [minX, maxN - (t - w - h - w)];
}

function closeChainsAlongBorder(openChains, dir) {
  const { minX, maxX, minN, maxN } = RECT;
  const P = 2 * (maxX - minX) + 2 * (maxN - minN);
  const CORNERS = [0, maxX - minX, (maxX - minX) + (maxN - minN), 2 * (maxX - minX) + (maxN - minN)];
  const unused = openChains.map(c => ({ pts: c, t0: perimeterT(c[0]), t1: perimeterT(c[c.length - 1]) }));
  const polys = [];

  while (unused.length) {
    const first = unused.pop();
    const ring = first.pts.slice();
    const startT = first.t0;
    let curT = first.t1;

    for (let guard = 0; guard < 100; guard++) {
      // Distance along the border from curT in direction dir to each candidate.
      const ahead = t => ((dir * (t - curT)) % P + P) % P;
      let best = null, bestD = ahead(startT) || P; // closing back to start
      for (let i = 0; i < unused.length; i++) {
        for (const end of [0, 1]) {
          const t = end === 0 ? unused[i].t0 : unused[i].t1;
          const d = ahead(t);
          if (d < bestD) { bestD = d; best = { i, end }; }
        }
      }
      // Insert any bbox corners passed while walking the border.
      const targetT = best === null ? startT : (best.end === 0 ? unused[best.i].t0 : unused[best.i].t1);
      for (const c of CORNERS.map(t => ({ t, d: ((dir * (t - curT)) % P + P) % P }))
                            .filter(c => c.d > EPS && c.d < bestD - EPS)
                            .sort((a, b) => a.d - b.d)) {
        ring.push(perimeterPoint(c.t));
      }
      if (best === null) break; // close the ring
      const next = unused.splice(best.i, 1)[0];
      const pts = best.end === 0 ? next.pts : next.pts.slice().reverse();
      ring.push(...pts);
      curT = best.end === 0 ? next.t1 : next.t0;
    }
    ring.push(ring[0]);
    if (ring.length >= 4) polys.push(ring);
  }
  return polys;
}

function closeClipped(openChains) {
  if (!openChains.length) return [];
  const cw = closeChainsAlongBorder(openChains, 1);
  const ccw = closeChainsAlongBorder(openChains, -1);
  const area = polys => polys.reduce((s, r) => s + Math.abs(ringArea(r)), 0);
  return area(cw) <= area(ccw) ? cw : ccw;
}

// ---------------------------------------------------------------------------
// Element classification

function parseHeight(tags, id) {
  const h = parseFloat(String(tags.height || tags['building:height'] || '').replace(/m.*/, ''));
  if (!isNaN(h) && h > 0) return h;
  const levels = parseFloat(tags['building:levels']);
  if (!isNaN(levels) && levels > 0) return levels * 3.1 + 2;
  // Deterministic pseudo-random default so the skyline isn't uniform.
  const jitter = (id * 2654435761 % 97) / 97;
  return 9 + jitter * 9;
}

const ROAD_CLASSES = {
  motorway: { w: 14, col: 0x3a3d44 }, trunk: { w: 13, col: 0x3a3d44 },
  primary: { w: 12, col: 0x40434a }, secondary: { w: 10, col: 0x44474e },
  tertiary: { w: 8, col: 0x44474e }, unclassified: { w: 6.5, col: 0x494c53 },
  residential: { w: 6.5, col: 0x494c53 }, living_street: { w: 5.5, col: 0x53565c },
  service: { w: 4, col: 0x505359 }, pedestrian: { w: 6, col: 0x8d8a80 },
  footway: { w: 2.5, col: 0x94917f }, path: { w: 2, col: 0x94917f },
  steps: { w: 2.5, col: 0x8d8a80 }, cycleway: { w: 2.5, col: 0x6e6058 },
  track: { w: 3, col: 0x7d7466 }, bridleway: { w: 3, col: 0x7d7466 },
};
for (const k of ['motorway', 'trunk', 'primary', 'secondary', 'tertiary']) {
  ROAD_CLASSES[k + '_link'] = ROAD_CLASSES[k];
}
const MINOR = new Set(['pedestrian', 'footway', 'path', 'steps', 'cycleway', 'track', 'bridleway']);

const GREEN_LEISURE = /^(park|garden|pitch|playground|recreation_ground)$/;
const GREEN_LANDUSE = /^(grass|meadow|forest|village_green|recreation_ground)$/;
const GREEN_NATURAL = /^(wood|scrub)$/;

function classify(el) {
  const t = el.tags || {};
  if (t.building && t.building !== 'no') return 'building';
  if (t.highway && ROAD_CLASSES[t.highway] && t.tunnel !== 'yes') return 'road';
  if (t.natural === 'water' || t.waterway === 'riverbank' || t.water) return 'water';
  if (GREEN_LEISURE.test(t.leisure || '') || GREEN_LANDUSE.test(t.landuse || '') ||
      GREEN_NATURAL.test(t.natural || '')) return 'green';
  return null;
}

// Turn a way or multipolygon relation into polygons: [{outer, holes}].
function elementPolygons(el, clippedOK) {
  const polys = [];
  if (el.type === 'way') {
    const { closed, open } = stitchChains(toChains(el.geometry));
    for (const ring of closed) polys.push({ outer: ring, holes: [] });
    if (clippedOK && open.length) {
      for (const ring of closeClipped(open)) polys.push({ outer: ring, holes: [] });
    }
  } else if (el.type === 'relation') {
    const outerChains = [], innerClosed = [];
    for (const m of el.members || []) {
      if (m.type !== 'way' || !m.geometry) continue;
      const chains = toChains(m.geometry);
      if (m.role === 'inner') {
        for (const c of stitchChains(chains).closed) innerClosed.push(c);
      } else {
        outerChains.push(...chains);
      }
    }
    const { closed, open } = stitchChains(outerChains);
    const outers = closed.slice();
    if (clippedOK && open.length) outers.push(...closeClipped(open));
    for (const outer of outers) {
      const holes = innerClosed.filter(h => pointInRing(h[0], outer));
      polys.push({ outer, holes });
    }
  }
  return polys;
}

// ---------------------------------------------------------------------------
// Geometry builders (world space: x east, y up, z = -north)

function shapeFromPoly(poly) {
  const shape = new THREE.Shape(poly.outer.map(([x, n]) => new THREE.Vector2(x, n)));
  for (const h of poly.holes) {
    shape.holes.push(new THREE.Path(h.map(([x, n]) => new THREE.Vector2(x, n))));
  }
  return shape;
}

function withColor(geo, hex) {
  geo.deleteAttribute('uv');
  const count = geo.getAttribute('position').count;
  const col = new THREE.Color(hex);
  const arr = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) { arr[i * 3] = col.r; arr[i * 3 + 1] = col.g; arr[i * 3 + 2] = col.b; }
  geo.setAttribute('color', new THREE.BufferAttribute(arr, 3));
  return geo;
}

const BUILDING_PALETTE = [0xb9a68c, 0xc7b299, 0xa89882, 0xcfc4ad, 0xb0a08e, 0x9e8f7d, 0xd6cab2, 0xbfae94];

function buildBuildings(elements) {
  const geos = [];
  for (const el of elements) {
    const height = parseHeight(el.tags || {}, el.id || 0);
    for (const poly of elementPolygons(el, false)) {
      if (Math.abs(ringArea(poly.outer)) < 4) continue;
      try {
        const geo = new THREE.ExtrudeGeometry(shapeFromPoly(poly), {
          depth: height, bevelEnabled: false, curveSegments: 1,
        });
        geo.rotateX(-Math.PI / 2); // shape plane (x, north) -> world (x, y-up, -z)
        withColor(geo, BUILDING_PALETTE[(el.id || 0) % BUILDING_PALETTE.length]);
        geos.push(geo);
      } catch (_) { /* skip degenerate footprints */ }
    }
  }
  return geos.length ? mergeGeometries(geos, false) : null;
}

function buildFlatPolys(elements, y, fallbackColor, clippedOK) {
  const geos = [];
  for (const el of elements) {
    for (const poly of elementPolygons(el, clippedOK)) {
      if (Math.abs(ringArea(poly.outer)) < 4) continue;
      try {
        const geo = new THREE.ShapeGeometry(shapeFromPoly(poly), 1);
        geo.rotateX(-Math.PI / 2);
        geo.translate(0, y, 0);
        withColor(geo, fallbackColor);
        geos.push(geo);
      } catch (_) {}
    }
  }
  return geos.length ? mergeGeometries(geos, false) : null;
}

// Road ribbon: constant-width strip along the polyline.
function ribbonGeometry(points, width, y) {
  const hw = width / 2;
  const n = points.length;
  const pos = [], idx = [];
  let prevDir = null;
  for (let i = 0; i < n; i++) {
    const p = points[i];
    const dNext = i < n - 1 ? norm2(sub2(points[i + 1], p)) : null;
    const dPrev = i > 0 ? norm2(sub2(p, points[i - 1])) : null;
    let dir = dNext && dPrev ? norm2([dNext[0] + dPrev[0], dNext[1] + dPrev[1]]) : (dNext || dPrev);
    if (!dir || (!dir[0] && !dir[1])) dir = prevDir || [1, 0];
    prevDir = dir;
    // Miter scale, clamped so sharp corners don't explode.
    let scale = 1;
    if (dNext && dPrev) {
      const dot = dNext[0] * dPrev[0] + dNext[1] * dPrev[1];
      scale = Math.min(1 / Math.max(Math.sqrt((1 + dot) / 2), 0.5), 2);
    }
    const perp = [-dir[1] * hw * scale, dir[0] * hw * scale];
    pos.push(p[0] + perp[0], y, -(p[1] + perp[1]));
    pos.push(p[0] - perp[0], y, -(p[1] - perp[1]));
  }
  for (let i = 0; i < n - 1; i++) {
    const a = i * 2;
    idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  const normals = new Float32Array(pos.length);
  for (let i = 0; i < pos.length; i += 3) normals[i + 1] = 1;
  geo.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  geo.setIndex(idx);
  return geo;
}
const sub2 = (a, b) => [a[0] - b[0], a[1] - b[1]];
function norm2(v) { const l = Math.hypot(v[0], v[1]); return l > 1e-9 ? [v[0] / l, v[1] / l] : null; }

function buildRoads(elements) {
  const geos = [];
  for (const el of elements) {
    const cls = ROAD_CLASSES[el.tags.highway];
    const y = MINOR.has(el.tags.highway) ? 0.45 : 0.35;
    for (const chain of toChains(el.geometry)) {
      const geo = ribbonGeometry(chain, cls.w, y);
      withColor(geo, cls.col);
      geos.push(geo.toNonIndexed());
    }
  }
  return geos.length ? mergeGeometries(geos, false) : null;
}

// ---------------------------------------------------------------------------
// Scene assembly

function buildScene(osm) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xbfe0f5);
  scene.fog = new THREE.Fog(0xbfe0f5, 400, 2600);

  scene.add(new THREE.HemisphereLight(0xe8f2ff, 0x8a8474, 1.05));
  const sun = new THREE.DirectionalLight(0xfff2dd, 1.6);
  sun.position.set(-0.55, 1, 0.35).multiplyScalar(800);
  scene.add(sun);

  const groundSize = Math.max(RECT.maxX - RECT.minX, RECT.maxN - RECT.minN) + 3000;
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(groundSize, groundSize),
    new THREE.MeshLambertMaterial({ color: 0xa8a396 })
  );
  ground.rotation.x = -Math.PI / 2;
  scene.add(ground);

  const byClass = { building: [], road: [], green: [], water: [] };
  const relationMemberWays = new Set();
  for (const el of osm.elements || []) {
    if (el.type === 'relation' && classify(el) === 'building') {
      for (const m of el.members || []) if (m.type === 'way') relationMemberWays.add(m.ref);
    }
  }
  for (const el of osm.elements || []) {
    const cls = classify(el);
    if (!cls) continue;
    // Skip outline ways duplicated by a building relation (avoids z-fighting).
    if (cls === 'building' && el.type === 'way' && relationMemberWays.has(el.id)) continue;
    byClass[cls].push(el);
  }

  const lambert = () => new THREE.MeshLambertMaterial({ vertexColors: true });
  const counts = {};

  const green = buildFlatPolys(byClass.green, 0.05, 0x7fae6a, true);
  if (green) scene.add(new THREE.Mesh(green, lambert()));
  const water = buildFlatPolys(byClass.water, 0.20, 0x5d84a8, true);
  if (water) scene.add(new THREE.Mesh(water, lambert()));
  const roads = buildRoads(byClass.road);
  if (roads) scene.add(new THREE.Mesh(roads, lambert()));
  const buildings = buildBuildings(byClass.building);
  if (buildings) scene.add(new THREE.Mesh(buildings, lambert()));

  counts.buildings = byClass.building.length;
  counts.roads = byClass.road.length;
  counts.green = byClass.green.length;
  counts.water = byClass.water.length;
  return { scene, counts };
}

// ---------------------------------------------------------------------------
// Controls: first-person walk + simple arcade drive

const EYE_WALK = 1.7, EYE_DRIVE = 1.45;

class Player {
  constructor(camera) {
    this.camera = camera;
    this.mode = 'walk';
    this.yaw = 0; this.pitch = 0;
    this.pos = new THREE.Vector3();
    this.heading = 0;     // vehicle heading (drive mode)
    this.speed = 0;       // signed m/s (drive mode)
    this.lookOffset = 0;  // mouse-look offset from vehicle heading
    this.keys = new Set();
    this.respawn();
  }

  respawn() {
    const p = project(SPAWN.lat, SPAWN.lon);
    this.pos.set(p.x, EYE_WALK, -p.n);
    this.yaw = SPAWN.heading; this.heading = SPAWN.heading;
    this.pitch = 0; this.speed = 0; this.lookOffset = 0;
  }

  onMouse(dx, dy) {
    const s = 0.0023;
    if (this.mode === 'walk') this.yaw -= dx * s;
    else this.lookOffset -= dx * s;
    this.pitch = Math.max(-1.35, Math.min(1.35, this.pitch - dy * s));
  }

  toggleMode() {
    if (this.mode === 'walk') {
      this.mode = 'drive';
      this.heading = this.yaw; this.lookOffset = 0; this.speed = 0;
    } else {
      this.mode = 'walk';
      this.yaw = this.heading + this.lookOffset; this.speed = 0;
    }
  }

  update(dt) {
    const k = this.keys;
    if (this.mode === 'walk') {
      const speed = k.has('ShiftLeft') || k.has('ShiftRight') ? 13 : 5.5;
      const f = (k.has('KeyW') ? 1 : 0) - (k.has('KeyS') ? 1 : 0);
      const r = (k.has('KeyD') ? 1 : 0) - (k.has('KeyA') ? 1 : 0);
      if (f || r) {
        const len = Math.hypot(f, r);
        const sin = Math.sin(this.yaw), cos = Math.cos(this.yaw);
        // forward = (-sin(yaw), -cos(yaw)) in (x, z); right = (cos, -sin)
        this.pos.x += ((-sin * f) + (cos * r)) / len * speed * dt;
        this.pos.z += ((-cos * f) + (-sin * r)) / len * speed * dt;
      }
      this.pos.y = EYE_WALK;
      this.camera.rotation.set(0, 0, 0, 'YXZ');
      this.camera.rotation.y = this.yaw;
      this.camera.rotation.x = this.pitch;
    } else {
      const throttle = (k.has('KeyW') ? 1 : 0) - (k.has('KeyS') ? 1 : 0);
      const accel = throttle > 0 ? 9 : throttle < 0 ? (this.speed > 0.5 ? -14 : -5) : 0;
      this.speed += accel * dt;
      this.speed -= this.speed * 0.35 * dt;                 // drag
      this.speed = Math.max(-7, Math.min(26, this.speed));
      if (!throttle && Math.abs(this.speed) < 0.3) this.speed = 0;

      const steer = (k.has('KeyA') ? 1 : 0) - (k.has('KeyD') ? 1 : 0);
      const steerRate = 1.9 * Math.min(Math.abs(this.speed) / 9, 1) * Math.sign(this.speed || 1);
      this.heading += steer * steerRate * dt;

      this.pos.x += -Math.sin(this.heading) * this.speed * dt;
      this.pos.z += -Math.cos(this.heading) * this.speed * dt;
      this.pos.y = EYE_DRIVE;

      // Mouse-look drifts back to straight ahead while moving.
      if (Math.abs(this.speed) > 2) this.lookOffset *= Math.max(0, 1 - 1.8 * dt);
      this.camera.rotation.set(0, 0, 0, 'YXZ');
      this.camera.rotation.y = this.heading + this.lookOffset;
      this.camera.rotation.x = this.pitch;
    }
    // Keep the player inside the loaded area (with a small margin).
    this.pos.x = Math.max(RECT.minX - 50, Math.min(RECT.maxX + 50, this.pos.x));
    this.pos.z = Math.max(-RECT.maxN - 50, Math.min(-RECT.minN + 50, this.pos.z));
    this.camera.position.copy(this.pos);
  }
}

// ---------------------------------------------------------------------------
// Bootstrap

async function main() {
  if (location.protocol === 'file:') return; // index.html already showed the hint

  let osm;
  try {
    osm = await loadData();
  } catch (err) {
    setStatus('Failed to load map data.');
    showError(String(err.message || err));
    return;
  }

  setStatus('Building geometry…');
  await new Promise(r => setTimeout(r)); // let the status paint
  const { scene, counts } = buildScene(osm);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(innerWidth, innerHeight);
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  document.getElementById('app').appendChild(renderer.domElement);

  const camera = new THREE.PerspectiveCamera(72, innerWidth / innerHeight, 0.3, 5000);
  const player = new Player(camera);

  addEventListener('resize', () => {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });

  const overlay = document.getElementById('overlay');
  const startBtn = document.getElementById('start');
  setStatus(`${counts.buildings.toLocaleString()} buildings · ${counts.roads.toLocaleString()} road segments loaded`);
  startBtn.style.display = 'inline-block';

  if (params.has('autostart')) { // used by the automated smoke test
    overlay.style.display = 'none';
    document.getElementById('hud-left').hidden = false;
  }

  startBtn.addEventListener('click', () => renderer.domElement.requestPointerLock());
  document.addEventListener('pointerlockchange', () => {
    const locked = document.pointerLockElement === renderer.domElement;
    overlay.style.display = locked ? 'none' : 'flex';
    document.getElementById('hud-left').hidden = !locked;
  });
  document.addEventListener('mousemove', e => {
    if (document.pointerLockElement === renderer.domElement) player.onMouse(e.movementX, e.movementY);
  });
  document.addEventListener('keydown', e => {
    if (e.code === 'KeyC') player.toggleMode();
    if (e.code === 'KeyR') player.respawn();
    player.keys.add(e.code);
  });
  document.addEventListener('keyup', e => player.keys.delete(e.code));

  const hudMode = document.getElementById('hud-mode');
  const hudInfo = document.getElementById('hud-info');
  let hudTimer = 0;

  const clock = new THREE.Clock();
  renderer.setAnimationLoop(() => {
    const dt = Math.min(clock.getDelta(), 0.05);
    player.update(dt);
    hudTimer -= dt;
    if (hudTimer <= 0) {
      hudTimer = 0.25;
      const { lat, lon } = unproject(player.pos.x, -player.pos.z);
      const mph = Math.abs(player.speed) * 2.23694;
      hudMode.textContent = player.mode === 'walk' ? 'WALK  (C to drive)' : `DRIVE  ${mph.toFixed(0)} mph  (C to walk)`;
      hudInfo.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    }
    renderer.render(scene, camera);
  });

  // Expose for the automated smoke test.
  window.__demo = { counts, player, ready: true };
}

main();
