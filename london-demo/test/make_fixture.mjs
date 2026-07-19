// Generates test/fixture.json — a small SYNTHETIC dataset in raw Overpass
// JSON format, used by the automated smoke test (?data=test/fixture.json).
// It is not real London data; it exists so the render pipeline can be
// exercised offline. Run: node make_fixture.mjs
import { writeFileSync } from 'node:fs';

// Must sit inside DEFAULT_BBOX in main.js: 51.4975,-0.1360,51.5095,-0.1160
const SPAWN = { lat: 51.5006, lon: -0.1270 };
const DEG_LAT = 1 / 111132;                       // ~1 metre in degrees
const DEG_LON = 1 / (111320 * Math.cos((51.5035 * Math.PI) / 180));

const at = (n, e) => ({ lat: SPAWN.lat + n * DEG_LAT, lon: SPAWN.lon + e * DEG_LON });
let nextId = 1000;
const elements = [];

function rect(n, e, dn, de) {
  return [at(n, e), at(n, e + de), at(n + dn, e + de), at(n + dn, e), at(n, e)];
}

// Grid of simple building ways flanking a north-south street.
for (let row = 0; row < 6; row++) {
  for (const side of [-1, 1]) {
    elements.push({
      type: 'way', id: nextId++,
      tags: { building: 'yes', 'building:levels': String(2 + ((row * 3 + side + 3) % 5)) },
      geometry: rect(20 + row * 35, side === -1 ? -38 : 14, 24, 24),
    });
  }
}
// One tall landmark with an explicit height tag.
elements.push({
  type: 'way', id: nextId++,
  tags: { building: 'tower', height: '96' },
  geometry: rect(230, -10, 18, 18),
});
// A building multipolygon relation with a courtyard hole.
elements.push({
  type: 'relation', id: nextId++,
  tags: { building: 'yes', type: 'multipolygon', 'building:levels': '4' },
  members: [
    { type: 'way', ref: 1, role: 'outer', geometry: rect(20, 50, 70, 70) },
    { type: 'way', ref: 2, role: 'inner', geometry: rect(40, 70, 30, 30) },
  ],
});
// Roads: the main street plus a crossing footpath.
elements.push({
  type: 'way', id: nextId++, tags: { highway: 'primary', name: 'Test Street' },
  geometry: [at(-40, 0), at(120, 0), at(300, 4)],
});
elements.push({
  type: 'way', id: nextId++, tags: { highway: 'footway' },
  geometry: [at(100, -60), at(100, 60)],
});
// A park with a closed pond.
elements.push({
  type: 'way', id: nextId++, tags: { leisure: 'park' },
  geometry: rect(-10, -160, 180, 100),
});
elements.push({
  type: 'way', id: nextId++, tags: { natural: 'water' },
  geometry: rect(40, -130, 50, 40),
});
// An open water chain whose ends touch the bbox east edge — exercises the
// clipped-polygon closure (like the Thames crossing the real bbox).
const EAST_EDGE_LON = -0.1160;
elements.push({
  type: 'way', id: nextId++, tags: { natural: 'water' },
  geometry: [
    { lat: 51.5060, lon: EAST_EDGE_LON },
    { lat: 51.5058, lon: -0.1200 },
    { lat: 51.5048, lon: -0.1200 },
    { lat: 51.5046, lon: EAST_EDGE_LON },
  ],
});

writeFileSync(new URL('./fixture.json', import.meta.url),
  JSON.stringify({ version: 0.6, generator: 'synthetic-fixture', elements }, null, 1));
console.log(`wrote fixture.json with ${elements.length} elements`);
