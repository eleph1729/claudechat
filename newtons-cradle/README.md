# Newton's Cradle

A fully 3D, physically exact Newton's cradle. Open `index.html` in any
browser — no build step, no network needed (Three.js r160 is vendored).

## Interaction

- **Drag any ball** to pull it out — including a middle ball straight into
  the pack, which shoves its neighbours ahead of it. Release with a flick to
  throw it.
- **Drag the dark** background to orbit the camera; **scroll / pinch** to zoom.
- **Lift 1 · 2 · 3** buttons raise that many balls and let them drop — the
  same number swings out on the far side.
- **Sound** — impact clicks are synthesized live with WebAudio, panned to
  where the collision happened and scaled by impact strength.

## Physics

Each ball is a rigid pendulum constrained to the swing plane by its pair of
V-strings, integrated with semi-implicit Euler at 600 Hz. Ball–ball impacts
are resolved as sequential elastic impulses (restitution 0.985, equal
masses) iterated along the chain, which is exactly why "two in, two out"
emerges instead of being scripted. Positional de-penetration is projected
back onto each pendulum's arc so the constraint is never violated.

## Rendering

Three.js with ACES tone mapping. The chrome reflects a procedural studio —
black room with white, ice-blue, amber and teal light strips — baked with
PMREM. The floor mirror is the classic trick: the whole cradle cloned,
flipped and dimmed beneath a semi-transparent floor. Soft PCF shadows,
fog, and a gradient dome round it out.
