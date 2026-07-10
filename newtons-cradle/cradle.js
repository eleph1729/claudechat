/* Newton's Cradle — a physically exact, fully 3D toy.
 *
 * Physics model: each ball is a rigid pendulum swinging in the x/y plane
 * (the pair of V-strings constrains it there, exactly as in the real toy).
 * Ball–ball impacts are resolved as sequential elastic impulses between
 * equal masses, which is what makes "two in, two out" emerge on its own.
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------- constants
  const N = 5;                    // number of balls
  const R = 0.5;                  // ball radius
  const GAP = 0.0012;             // hair's-width rest gap between balls
  const SPACING = 2 * R + GAP;    // rest distance between neighbouring centres
  const L = 3.2;                  // effective pendulum length (pivot → centre)
  const PIVOT_Y = 4.4;            // height of the pivot line
  const RAIL_Z = 1.4;             // half-distance between the two rails
  const GRAVITY = 9.81;
  const RESTITUTION = 0.985;      // hardened steel
  const AIR_DRAG = 0.045;         // per-second angular velocity decay
  const MAX_ANGLE = 1.35;         // drag clamp, radians from vertical
  const PHYS_DT = 1 / 600;        // physics substep
  const REST_X = [];              // rest x of each ball centre
  for (let i = 0; i < N; i++) REST_X.push((i - (N - 1) / 2) * SPACING);

  // ---------------------------------------------------------------- state
  const theta = new Float64Array(N);   // angle from vertical (x/y plane)
  const omega = new Float64Array(N);   // angular velocity
  const kinematic = new Uint8Array(N); // 1 → driven by hand / animation

  let dragIndex = -1;
  let dragOmega = 0;               // smoothed velocity while dragging
  let liftAnim = null;             // {indices, from[], to, t, dur}
  let soundOn = true;

  const ballX = i => REST_X[i] + L * Math.sin(theta[i]);
  const ballY = i => PIVOT_Y - L * Math.cos(theta[i]);

  // ---------------------------------------------------------------- renderer
  const canvas = document.getElementById("scene");
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.12;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x05060a, 26, 60);

  const camera = new THREE.PerspectiveCamera(
    42, window.innerWidth / window.innerHeight, 0.1, 200);

  // ------------------------------------------------------------- environment
  // A tiny studio built only to be photographed by PMREM: black room with
  // long light strips. It is what the chrome actually reflects.
  function buildEnvironment() {
    const env = new THREE.Scene();
    env.background = new THREE.Color(0x000000);

    const strip = (w, h, color, intensity, pos, look) => {
      const m = new THREE.Mesh(
        new THREE.PlaneGeometry(w, h),
        new THREE.MeshBasicMaterial({
          color: new THREE.Color(color).multiplyScalar(intensity),
          side: THREE.DoubleSide
        }));
      m.position.copy(pos);
      m.lookAt(look || new THREE.Vector3(0, 0, 0));
      env.add(m);
    };

    // big soft key overhead
    strip(14, 4, 0xffffff, 9, new THREE.Vector3(0, 9, 2));
    // long cool strip, back-left — the signature streak in the chrome
    strip(20, 1.4, 0x9fc8ff, 7, new THREE.Vector3(-6, 5, -9));
    // warm amber counter-strip, right
    strip(3.5, 9, 0xffc17a, 5.5, new THREE.Vector3(10, 4, 3));
    // faint teal kicker, low front
    strip(16, 1.0, 0x59fff2, 2.2, new THREE.Vector3(0, 1.0, 11));
    // dim violet wash behind
    strip(9, 6, 0x8a7bff, 1.1, new THREE.Vector3(3, 3, -12));
    // floor bounce so the underside of the spheres isn't dead black
    strip(24, 24, 0x1a1e26, 1.0, new THREE.Vector3(0, -4, 0),
          new THREE.Vector3(0, 0, 0));

    const pmrem = new THREE.PMREMGenerator(renderer);
    const tex = pmrem.fromScene(env, 0.035).texture;
    pmrem.dispose();
    return tex;
  }
  scene.environment = buildEnvironment();

  // Background: a huge gradient dome, independent of the reflection map.
  {
    const geo = new THREE.SphereGeometry(90, 32, 24);
    const mat = new THREE.ShaderMaterial({
      side: THREE.BackSide,
      depthWrite: false,
      fog: false,
      uniforms: {},
      vertexShader: `
        varying vec3 vDir;
        void main() {
          vDir = normalize(position);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }`,
      fragmentShader: `
        varying vec3 vDir;
        void main() {
          float h = clamp(vDir.y * 0.5 + 0.5, 0.0, 1.0);
          vec3 low  = vec3(0.012, 0.014, 0.022);
          vec3 mid  = vec3(0.030, 0.038, 0.058);
          vec3 high = vec3(0.010, 0.012, 0.020);
          vec3 c = mix(low, mid, smoothstep(0.35, 0.55, h));
          c = mix(c, high, smoothstep(0.60, 1.0, h));
          // faint cool glow on the horizon behind the cradle
          float glow = exp(-pow((h - 0.52) * 9.0, 2.0));
          c += vec3(0.012, 0.020, 0.034) * glow;
          gl_FragColor = vec4(c, 1.0);
        }`
    });
    scene.add(new THREE.Mesh(geo, mat));
  }

  // ------------------------------------------------------------------ lights
  const key = new THREE.DirectionalLight(0xfff4e6, 2.6);
  key.position.set(5, 10, 5);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.left = -7; key.shadow.camera.right = 7;
  key.shadow.camera.top = 8;   key.shadow.camera.bottom = -3;
  key.shadow.camera.near = 2;  key.shadow.camera.far = 26;
  key.shadow.bias = -0.0004;
  key.shadow.radius = 6;
  scene.add(key);

  const rim = new THREE.DirectionalLight(0x8fb8ff, 1.1);
  rim.position.set(-7, 4, -6);
  scene.add(rim);

  scene.add(new THREE.AmbientLight(0x20242e, 0.9));

  // ------------------------------------------------------------------- floor
  // Cheap-but-convincing mirror: the whole cradle is cloned, flipped in y and
  // dimmed, then a semi-transparent dark floor is laid over the flipped copy.
  const mirror = new THREE.Group();
  mirror.scale.y = -1;
  scene.add(mirror);

  {
    const c = document.createElement("canvas");
    c.width = c.height = 512;
    const g = c.getContext("2d");
    const grad = g.createRadialGradient(256, 256, 30, 256, 256, 256);
    grad.addColorStop(0.0, "#14171d");
    grad.addColorStop(0.55, "#0b0d12");
    grad.addColorStop(1.0, "#05060a");
    g.fillStyle = grad;
    g.fillRect(0, 0, 512, 512);
    const floorTex = new THREE.CanvasTexture(c);
    floorTex.colorSpace = THREE.SRGBColorSpace;

    const floor = new THREE.Mesh(
      new THREE.CircleGeometry(60, 72),
      new THREE.MeshStandardMaterial({
        map: floorTex,
        color: 0xffffff,
        roughness: 0.32,
        metalness: 0.75,
        envMapIntensity: 0.5,
        transparent: true,
        opacity: 0.86          // lets the mirrored clone bleed through
      }));
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    scene.add(floor);
  }

  // -------------------------------------------------------------- materials
  const chrome = new THREE.MeshStandardMaterial({
    color: 0xf4f6f8, metalness: 1.0, roughness: 0.045, envMapIntensity: 1.25
  });
  const frameMetal = new THREE.MeshStandardMaterial({
    color: 0xd8dce2, metalness: 1.0, roughness: 0.16, envMapIntensity: 1.0
  });
  const stringMetal = new THREE.MeshStandardMaterial({
    color: 0xb8bcc4, metalness: 0.9, roughness: 0.4
  });
  const mirrorTint = m => {
    const c = m.clone();
    c.color = c.color.clone().multiplyScalar(0.42);
    c.envMapIntensity *= 0.5;
    c.roughness = Math.min(1, c.roughness + 0.18);
    return c;
  };

  // ------------------------------------------------------------------- frame
  const frame = new THREE.Group();
  {
    const railR = 0.055, legR = 0.065;
    const railLen = (N - 1) * SPACING + 3.4;
    const railX = railLen / 2;

    const bar = (a, b, radius) => {
      const dir = new THREE.Vector3().subVectors(b, a);
      const len = dir.length();
      const m = new THREE.Mesh(
        new THREE.CylinderGeometry(radius, radius, len, 24), frameMetal);
      m.position.copy(a).addScaledVector(dir, 0.5);
      m.quaternion.setFromUnitVectors(
        new THREE.Vector3(0, 1, 0), dir.normalize());
      m.castShadow = true;
      frame.add(m);
      return m;
    };
    const knob = (p, r) => {
      const m = new THREE.Mesh(new THREE.SphereGeometry(r, 24, 16), frameMetal);
      m.position.copy(p);
      m.castShadow = true;
      frame.add(m);
    };

    for (const zs of [-1, 1]) {
      const z = RAIL_Z * zs;
      bar(new THREE.Vector3(-railX, PIVOT_Y, z),
          new THREE.Vector3(railX, PIVOT_Y, z), railR);
      knob(new THREE.Vector3(-railX, PIVOT_Y, z), railR * 1.9);
      knob(new THREE.Vector3(railX, PIVOT_Y, z), railR * 1.9);
    }
    // end crossbars
    for (const xs of [-1, 1]) {
      bar(new THREE.Vector3(railX * xs, PIVOT_Y, -RAIL_Z),
          new THREE.Vector3(railX * xs, PIVOT_Y, RAIL_Z), railR * 0.85);
    }
    // gently splayed legs with disc feet
    for (const xs of [-1, 1]) for (const zs of [-1, 1]) {
      const top = new THREE.Vector3(railX * xs, PIVOT_Y, RAIL_Z * zs);
      const foot = new THREE.Vector3((railX + 0.7) * xs, 0, (RAIL_Z + 0.55) * zs);
      bar(top, new THREE.Vector3().copy(foot).setY(0.04), legR);
      const disc = new THREE.Mesh(
        new THREE.CylinderGeometry(0.21, 0.24, 0.07, 28), frameMetal);
      disc.position.copy(foot).setY(0.035);
      disc.castShadow = true;
      frame.add(disc);
    }
  }
  scene.add(frame);

  // mirrored frame (static — clone once)
  {
    const fm = frame.clone(true);
    fm.traverse(o => {
      if (o.isMesh) { o.material = mirrorTint(o.material); o.castShadow = false; }
    });
    mirror.add(fm);
  }

  // ------------------------------------------------------------------- balls
  const ballGeo = new THREE.SphereGeometry(R, 96, 64);
  const balls = [], ballMirrors = [];
  for (let i = 0; i < N; i++) {
    const mat = chrome.clone();
    const m = new THREE.Mesh(ballGeo, mat);
    m.castShadow = true;
    m.userData.index = i;
    scene.add(m);
    balls.push(m);

    const mm = new THREE.Mesh(ballGeo, mirrorTint(chrome));
    mirror.add(mm);
    ballMirrors.push(mm);
  }

  // ----------------------------------------------------------------- strings
  const stringGeo = new THREE.CylinderGeometry(1, 1, 1, 8);
  const strings = [], stringMirrors = [];
  const anchors = [];
  for (let i = 0; i < N; i++) {
    for (const zs of [-1, 1]) {
      const anchor = new THREE.Vector3(REST_X[i], PIVOT_Y, RAIL_Z * zs);
      anchors.push(anchor);
      const s = new THREE.Mesh(stringGeo, stringMetal);
      s.castShadow = false;
      scene.add(s);
      strings.push(s);
      const sm = new THREE.Mesh(stringGeo, mirrorTint(stringMetal));
      mirror.add(sm);
      stringMirrors.push(sm);
    }
    // a small collar where the strings meet the ball
    const collar = new THREE.Mesh(
      new THREE.CylinderGeometry(0.06, 0.06, 0.05, 16), frameMetal);
    collar.userData.isCollar = i;
    scene.add(collar);
    balls[i].userData.collar = collar;
  }

  const STRING_R = 0.0075;
  const _dir = new THREE.Vector3(), _hook = new THREE.Vector3(),
        _up = new THREE.Vector3(0, 1, 0), _mid = new THREE.Vector3();

  function placeString(mesh, a, b) {
    _dir.subVectors(b, a);
    const len = _dir.length();
    _mid.copy(a).addScaledVector(_dir, 0.5);
    mesh.position.copy(_mid);
    mesh.scale.set(STRING_R, len, STRING_R);
    mesh.quaternion.setFromUnitVectors(_up, _dir.normalize());
  }

  function syncGraphics() {
    for (let i = 0; i < N; i++) {
      const bx = ballX(i), by = ballY(i);
      balls[i].position.set(bx, by, 0);
      ballMirrors[i].position.set(bx, by, 0);

      for (let k = 0; k < 2; k++) {
        const a = anchors[i * 2 + k];
        _hook.set(bx, by, 0);
        _dir.subVectors(a, _hook).normalize();
        _hook.addScaledVector(_dir, R * 0.99);
        const s = strings[i * 2 + k];
        placeString(s, a, _hook);
        const sm = stringMirrors[i * 2 + k];
        sm.position.copy(s.position);
        sm.quaternion.copy(s.quaternion);
        sm.scale.copy(s.scale);
      }
      // collar sits on top of the ball, aligned with the swing angle
      const collar = balls[i].userData.collar;
      collar.position.set(
        bx - Math.sin(theta[i]) * R, by + Math.cos(theta[i]) * R, 0);
      collar.rotation.z = theta[i];
    }
  }

  // ----------------------------------------------------------------- physics
  const impactAccum = new Float64Array(N - 1); // per-pair impulse this frame

  function stepPhysics(dt) {
    // free pendulum motion (semi-implicit Euler)
    const drag = Math.exp(-AIR_DRAG * dt);
    for (let i = 0; i < N; i++) {
      if (kinematic[i]) continue;
      omega[i] += -(GRAVITY / L) * Math.sin(theta[i]) * dt;
      omega[i] *= drag;
      theta[i] += omega[i] * dt;
    }

    // ball–ball impulses, iterated so momentum propagates down the chain
    const D = 2 * R;
    for (let iter = 0; iter < 10; iter++) {
      let hit = false;
      for (let i = 0; i < N - 1; i++) {
        const j = i + 1;
        const xi = ballX(i), yi = ballY(i);
        const xj = ballX(j), yj = ballY(j);
        let nx = xj - xi, ny = yj - yi;
        const dist = Math.hypot(nx, ny);
        if (dist >= D || dist === 0) continue;
        nx /= dist; ny /= dist;

        // velocities of the centres; tangent of pendulum i is (cosθ, sinθ)
        const ti_x = Math.cos(theta[i]), ti_y = Math.sin(theta[i]);
        const tj_x = Math.cos(theta[j]), tj_y = Math.sin(theta[j]);
        const vi = L * omega[i], vj = L * omega[j];
        const vrel = (vi * ti_x - vj * tj_x) * nx + (vi * ti_y - vj * tj_y) * ny;
        if (vrel <= 1e-9) continue;

        const wi = kinematic[i] ? 0 : 1;
        const wj = kinematic[j] ? 0 : 1;
        if (wi + wj === 0) continue;

        const e = vrel > 0.06 ? RESTITUTION : 0; // kill resting-contact chatter
        const Jm = (1 + e) * vrel / (wi + wj);   // impulse per unit mass

        if (wi) omega[i] -= (Jm * (nx * ti_x + ny * ti_y)) / L;
        if (wj) omega[j] += (Jm * (nx * tj_x + ny * tj_y)) / L;

        impactAccum[i] = Math.max(impactAccum[i], Jm * vrel > 0 ? vrel : 0);
        hit = true;
      }
      if (!hit) break;
    }

    // positional de-penetration, projected back onto each pendulum's arc
    for (let iter = 0; iter < 4; iter++) {
      let moved = false;
      for (let i = 0; i < N - 1; i++) {
        const j = i + 1;
        const xi = ballX(i), yi = ballY(i);
        const xj = ballX(j), yj = ballY(j);
        let nx = xj - xi, ny = yj - yi;
        const dist = Math.hypot(nx, ny);
        if (dist >= D - 1e-9 || dist === 0) continue;
        nx /= dist; ny /= dist;
        const pen = (D - dist) * 0.85;
        const wi = kinematic[i] ? 0 : 1;
        const wj = kinematic[j] ? 0 : 1;
        if (wi + wj === 0) continue;
        const share = pen / (wi + wj);
        if (wi) theta[i] -= (share * (nx * Math.cos(theta[i]) + ny * Math.sin(theta[i]))) / L;
        if (wj) theta[j] += (share * (nx * Math.cos(theta[j]) + ny * Math.sin(theta[j]))) / L;
        moved = true;
      }
      if (!moved) break;
    }
  }

  // ------------------------------------------------------------------- sound
  let audioCtx = null, masterGain = null;
  function ensureAudio() {
    if (audioCtx) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    audioCtx = new AC();
    const comp = audioCtx.createDynamicsCompressor();
    comp.threshold.value = -18;
    comp.ratio.value = 6;
    masterGain = audioCtx.createGain();
    masterGain.gain.value = 0.7;
    masterGain.connect(comp);
    comp.connect(audioCtx.destination);
  }

  let lastClick = 0;
  function clack(strength, pan) {
    if (!soundOn || !audioCtx || audioCtx.state !== "running") return;
    const now = audioCtx.currentTime;
    if (now - lastClick < 0.018) return;
    lastClick = now;

    const v = Math.min(1, strength / 3.2);
    const amp = 0.05 + 0.5 * Math.pow(v, 1.3);
    const out = audioCtx.createGain();
    out.gain.value = amp;
    const panner = audioCtx.createStereoPanner
      ? audioCtx.createStereoPanner() : null;
    if (panner) {
      panner.pan.value = Math.max(-0.7, Math.min(0.7, pan));
      out.connect(panner); panner.connect(masterGain);
    } else {
      out.connect(masterGain);
    }

    // bright metallic partials
    const partials = [
      [2650, 0.050, 1.0], [4380, 0.036, 0.55],
      [6900, 0.024, 0.30], [9400, 0.015, 0.14]
    ];
    for (const [f, dur, g] of partials) {
      const o = audioCtx.createOscillator();
      o.frequency.value = f * (0.97 + Math.random() * 0.06);
      const og = audioCtx.createGain();
      og.gain.setValueAtTime(g, now);
      og.gain.exponentialRampToValueAtTime(0.0008, now + dur);
      o.connect(og); og.connect(out);
      o.start(now); o.stop(now + dur + 0.02);
    }
    // low thock for body
    const o = audioCtx.createOscillator();
    o.frequency.setValueAtTime(340, now);
    o.frequency.exponentialRampToValueAtTime(160, now + 0.05);
    const og = audioCtx.createGain();
    og.gain.setValueAtTime(0.35, now);
    og.gain.exponentialRampToValueAtTime(0.001, now + 0.06);
    o.connect(og); og.connect(out);
    o.start(now); o.stop(now + 0.08);
  }

  // ------------------------------------------------------------- orbit camera
  const orbit = {
    theta: 0.55, phi: 1.12, radius: 11.5,
    tTheta: 0.55, tPhi: 1.12, tRadius: 11.5,
    target: new THREE.Vector3(0, 2.35, 0)
  };
  function updateCamera(dt) {
    const k = 1 - Math.exp(-dt * 9);
    orbit.theta += (orbit.tTheta - orbit.theta) * k;
    orbit.phi += (orbit.tPhi - orbit.phi) * k;
    orbit.radius += (orbit.tRadius - orbit.radius) * k;
    const sp = Math.sin(orbit.phi), cp = Math.cos(orbit.phi);
    camera.position.set(
      orbit.target.x + orbit.radius * sp * Math.sin(orbit.theta),
      orbit.target.y + orbit.radius * cp,
      orbit.target.z + orbit.radius * sp * Math.cos(orbit.theta));
    camera.lookAt(orbit.target);
  }
  const clampPhi = p => Math.max(0.22, Math.min(1.50, p));
  const clampRadius = r => Math.max(5.5, Math.min(24, r));

  // ------------------------------------------------------------- interaction
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const pointers = new Map(); // active pointers for pinch
  let orbiting = false;
  let pinchDist = 0;
  let hoverIndex = -1;

  function setNDC(e) {
    ndc.x = (e.clientX / window.innerWidth) * 2 - 1;
    ndc.y = -(e.clientY / window.innerHeight) * 2 + 1;
  }

  function pickBall(e) {
    setNDC(e);
    raycaster.setFromCamera(ndc, camera);
    const hits = raycaster.intersectObjects(balls, false);
    return hits.length ? hits[0].object.userData.index : -1;
  }

  // pointer ray ∩ swing plane (z = 0) → pendulum angle for ball i
  function pointerAngle(e, i) {
    setNDC(e);
    raycaster.setFromCamera(ndc, camera);
    const o = raycaster.ray.origin, d = raycaster.ray.direction;
    if (Math.abs(d.z) < 1e-4) return null;
    const t = -o.z / d.z;
    if (t <= 0) return null;
    const px = o.x + d.x * t, py = o.y + d.y * t;
    const dx = px - REST_X[i], dy = py - PIVOT_Y;
    if (dx * dx + dy * dy < 1e-6) return null;
    return Math.max(-MAX_ANGLE, Math.min(MAX_ANGLE, Math.atan2(dx, -dy)));
  }

  canvas.addEventListener("pointerdown", e => {
    ensureAudio();
    if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
    canvas.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (pointers.size === 2) {           // pinch begins: cancel modes
      const [a, b] = [...pointers.values()];
      pinchDist = Math.hypot(a.x - b.x, a.y - b.y);
      orbiting = false;
      return;
    }

    const hit = pickBall(e);
    if (hit >= 0) {
      dragIndex = hit;
      kinematic[hit] = 1;
      dragOmega = 0;
      liftAnim = null;
      const a = pointerAngle(e, hit);
      if (a !== null) theta[hit] = a;
      omega[hit] = 0;
    } else {
      orbiting = true;
    }
  });

  canvas.addEventListener("pointermove", e => {
    const prev = pointers.get(e.pointerId);
    if (prev && pointers.size === 2) {   // pinch zoom
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinchDist > 0) orbit.tRadius = clampRadius(orbit.tRadius * pinchDist / d);
      pinchDist = d;
      return;
    }

    if (dragIndex >= 0) {
      const a = pointerAngle(e, dragIndex);
      if (a !== null) {
        const dt = Math.max(1e-3, frameDt);
        const w = (a - theta[dragIndex]) / dt;
        dragOmega = dragOmega * 0.7 + w * 0.3;
        theta[dragIndex] = a;
        omega[dragIndex] = w; // so impacts while dragging feel right
      }
    } else if (orbiting && prev) {
      const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
      orbit.tTheta -= dx * 0.0055;
      orbit.tPhi = clampPhi(orbit.tPhi - dy * 0.0045);
    } else if (!("ontouchstart" in window)) {
      const h = pickBall(e);
      if (h !== hoverIndex) {
        if (hoverIndex >= 0) balls[hoverIndex].material.envMapIntensity = 1.25;
        if (h >= 0) balls[h].material.envMapIntensity = 1.7;
        hoverIndex = h;
        canvas.style.cursor = h >= 0 ? "grab" : "";
      }
    }
    if (prev) pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  });

  function endPointer(e) {
    pointers.delete(e.pointerId);
    if (dragIndex >= 0) {
      kinematic[dragIndex] = 0;
      omega[dragIndex] = Math.max(-6, Math.min(6, dragOmega));
      dragIndex = -1;
    }
    orbiting = false;
    pinchDist = 0;
  }
  canvas.addEventListener("pointerup", endPointer);
  canvas.addEventListener("pointercancel", endPointer);

  canvas.addEventListener("wheel", e => {
    e.preventDefault();
    orbit.tRadius = clampRadius(orbit.tRadius * (1 + Math.sign(e.deltaY) * 0.09));
  }, { passive: false });

  // --------------------------------------------------------------------- UI
  function reset() {
    liftAnim = null;
    for (let i = 0; i < N; i++) {
      if (i !== dragIndex) { theta[i] = 0; omega[i] = 0; kinematic[i] = 0; }
    }
  }

  function lift(n) {
    if (dragIndex >= 0) return;
    reset();
    const indices = [];
    for (let i = 0; i < n; i++) indices.push(i);
    liftAnim = {
      indices,
      from: indices.map(i => theta[i]),
      to: -0.95,
      t: 0,
      dur: 0.5,
      hold: 0.25
    };
    for (const i of indices) kinematic[i] = 1;
  }

  function stepLiftAnim(dt) {
    if (!liftAnim) return;
    liftAnim.t += dt;
    const a = liftAnim;
    if (a.t < a.dur) {
      const u = a.t / a.dur;
      const ease = 1 - Math.pow(1 - u, 3);
      a.indices.forEach((idx, k) => {
        theta[idx] = a.from[k] + (a.to - a.from[k]) * ease;
        omega[idx] = 0;
      });
    } else if (a.t < a.dur + a.hold) {
      a.indices.forEach(idx => { theta[idx] = a.to; omega[idx] = 0; });
    } else {
      a.indices.forEach(idx => { kinematic[idx] = 0; omega[idx] = 0; });
      liftAnim = null;
    }
  }

  document.querySelectorAll("[data-lift]").forEach(btn =>
    btn.addEventListener("click", () => {
      ensureAudio();
      if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
      lift(parseInt(btn.dataset.lift, 10));
    }));
  document.getElementById("btn-reset").addEventListener("click", reset);
  const soundBtn = document.getElementById("btn-sound");
  soundBtn.addEventListener("click", () => {
    ensureAudio();
    if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
    soundOn = !soundOn;
    soundBtn.textContent = soundOn ? "Sound On" : "Sound Off";
    soundBtn.classList.toggle("toggled-off", !soundOn);
  });

  window.addEventListener("resize", () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  // ------------------------------------------------------------- main loop
  // Start alive: the leftmost ball is already raised and about to drop.
  theta[0] = -0.85;

  let lastTime = performance.now();
  let accumulator = 0;
  let frameDt = 1 / 60;

  function tick(now) {
    requestAnimationFrame(tick);
    frameDt = Math.min(0.09, (now - lastTime) / 1000);
    lastTime = now;

    stepLiftAnim(frameDt);

    impactAccum.fill(0);
    accumulator += frameDt;
    let guard = 0;
    while (accumulator >= PHYS_DT && guard++ < 60) {
      stepPhysics(PHYS_DT);
      accumulator -= PHYS_DT;
    }
    if (guard >= 60) accumulator = 0;

    // one click per colliding pair per frame, panned to where it happened
    for (let i = 0; i < N - 1; i++) {
      if (impactAccum[i] > 0.12) {
        clack(impactAccum[i], (REST_X[i] + SPACING / 2) / 4);
      }
    }

    updateCamera(frameDt);
    syncGraphics();
    renderer.render(scene, camera);
  }

  syncGraphics();
  updateCamera(1);
  requestAnimationFrame(tick);
  document.getElementById("loading").classList.add("done");

  // tiny debug/testing handle (also handy in the console)
  window.__cradle = {
    theta, omega,
    screenXY(i) {
      const v = new THREE.Vector3(ballX(i), ballY(i), 0).project(camera);
      return {
        x: (v.x + 1) / 2 * window.innerWidth,
        y: (1 - v.y) / 2 * window.innerHeight
      };
    }
  };
})();
