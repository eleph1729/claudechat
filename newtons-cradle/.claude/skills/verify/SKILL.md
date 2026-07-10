---
name: verify
description: Verify the Newton's cradle demo (newtons-cradle/) renders and simulates correctly by driving it in headless Chromium.
---

# Verifying newtons-cradle

Static page, no build step. Load `newtons-cradle/index.html` directly via
`file://` (relative `<script src>` works) or `http-server`.

Drive it with the globally installed Playwright:

```bash
NODE_PATH=/opt/node22/lib/node_modules node <driver>.js
```

Launch Chromium with software GL:

```js
chromium.launch({
  executablePath: '/opt/pw-browsers/chromium',
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader']
})
```

Gotchas learned the hard way:

- **SwiftShader is ~1–4 fps.** The app clamps simulated time to 90 ms per
  rendered frame, so headless playback runs in heavy slow motion. This is NOT
  a physics bug — use a small viewport (≈560×360) and wait tens of seconds of
  wall time for seconds of sim time. Measure fps first if in doubt.
- `window.__cradle` exposes `theta`, `omega` (Float64Array per ball) and
  `screenXY(i)` → screen-pixel centre of ball *i*. Use it to aim mouse drags
  and to assert physics numerically instead of eyeballing screenshots.
- Good assertions: pull ball 4, release → max |theta[0]| grows large while
  max |theta[1..3]| stays ≈ 0 (momentum passes through). "Lift 3" button →
  three balls swing out on the far side (screenshot check).
- The `#loading` overlay keeps `pointer-events` until the main script's last
  line adds class `done`; if clicks are intercepted by it, the script threw —
  check `pageerror`.
