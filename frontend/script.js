/* ==========================================================================
   JARVIS FRONTEND — script.js
   --------------------------------------------------------------------------
   Single-file build. Sections below (search for the banner comments):

     1. STATE      — IDLE / LISTENING / THINKING / SPEAKING state machine
     2. AUDIO      — Web Audio API analyser (real + synthetic paths)
     3. SCENE      — Three.js core visualization + animation loop
     4. BACKEND    — fetch() wrappers for the existing FastAPI /chat, /speak
     5. VOICE      — text-to-speech (Web Speech API + backend pyttsx3)
     6. UI         — transcript readout, History panel, Settings panel
     7. MAIN       — wires it all together: STT input, ask/answer flow

   Each section is still its own IIFE attaching one namespace to `window`
   (JarvisState, JarvisAudio, JarvisScene, JarvisBackend, JarvisVoice,
   JarvisUI) — merging into one file didn't merge their internals. A
   section only ever calls another section's public functions.

   Requires vendor/three.min.js loaded before this file.
   ========================================================================== */

/* ==========================================================================
   JARVIS STATE MACHINE
   --------------------------------------------------------------------------
   Owns the single source of truth for what JARVIS is currently doing.
   Every other module (scene, audio, ui) reads state through this API
   instead of tracking it independently. This is what lets the visualizer,
   the status text, and the dock all agree with each other.

   Later, when the native "Hey Jarvis" listener is wired in (e.g. over a
   WebSocket or local IPC), it only needs to call JarvisState.set(...) —
   nothing else in the frontend has to change.
   ========================================================================== */

(function (global) {
  "use strict";

  var STATES = ["IDLE", "LISTENING", "THINKING", "SPEAKING"];

  /*
   * Target visual parameters per state. scene.js lerps its live values
   * toward whichever of these is active, so transitions are always smooth
   * regardless of how abruptly the state itself changes.
   *
   *  coreScale        - resting scale of the central core
   *  corePulse        - amplitude of the idle "breathing" pulse
   *  ringSpeed        - base rotation speed multiplier for concentric rings
   *  particleSpeed    - orbit speed multiplier for the particle field
   *  radialBase       - resting length (0-1) of the outer radial spokes
   *  radialVariance   - how much the spokes wobble/react on their own
   *  glow             - intensity (0-1) of the core + atmosphere glow
   *  particleOpacity  - baseline opacity of the particle field
   *  audioReactive    - whether the visualizer should respond to audio data
   *  scanline         - whether the "computation" scan sweep is visible
   */
  var STATE_CONFIG = {
    IDLE: {
      coreScale: 1.0,
      corePulse: 0.035,
      ringSpeed: 1.0,
      particleSpeed: 1.0,
      radialBase: 0.22,
      radialVariance: 0.06,
      glow: 0.55,
      particleOpacity: 0.35,
      audioReactive: false,
      scanline: false
    },
    LISTENING: {
      coreScale: 1.12,
      corePulse: 0.05,
      ringSpeed: 1.6,
      particleSpeed: 1.8,
      radialBase: 0.42,
      radialVariance: 0.16,
      glow: 0.85,
      particleOpacity: 0.6,
      audioReactive: false,
      scanline: false
    },
    THINKING: {
      coreScale: 1.05,
      corePulse: 0.08,
      ringSpeed: 3.2,
      particleSpeed: 0.6,
      radialBase: 0.3,
      radialVariance: 0.22,
      glow: 0.75,
      particleOpacity: 0.5,
      audioReactive: false,
      scanline: true
    },
    SPEAKING: {
      coreScale: 1.18,
      corePulse: 0.06,
      ringSpeed: 2.0,
      particleSpeed: 2.2,
      radialBase: 0.35,
      radialVariance: 0.55,
      glow: 1.0,
      particleOpacity: 0.75,
      audioReactive: true,
      scanline: false
    }
  };

  var current = "IDLE";
  var listeners = [];

  function isValid(state) {
    return STATES.indexOf(state) !== -1;
  }

  function set(state) {
    if (!isValid(state)) {
      console.warn("[JarvisState] Ignoring unknown state:", state);
      return;
    }
    if (state === current) return;

    var previous = current;
    current = state;

    for (var i = 0; i < listeners.length; i++) {
      try {
        listeners[i](current, previous);
      } catch (err) {
        console.error("[JarvisState] listener error:", err);
      }
    }
  }

  function get() {
    return current;
  }

  function getVisualTargets(state) {
    return STATE_CONFIG[state || current];
  }

  function onChange(callback) {
    if (typeof callback === "function") listeners.push(callback);
  }

  global.JarvisState = {
    STATES: STATES,
    set: set,
    get: get,
    getVisualTargets: getVisualTargets,
    onChange: onChange
  };
})(window);


/* ==========================================================================
   JARVIS AUDIO ANALYSIS
   --------------------------------------------------------------------------
   Bridges real audio into the visualizer via the Web Audio API.

   Two paths feed the visualizer, because the project supports two TTS
   modes (see backend.js / ui-voice wiring):

   1. BACKEND VOICE (/speak, pyttsx3) — a real <audio> element plays actual
      generated audio. We tap it with createMediaElementSource -> AnalyserNode
      and read genuine frequency-domain data every frame. This is the
      "real" path the spec describes:

          TTS audio -> <audio> -> Web Audio API -> AnalyserNode -> freq data

   2. BROWSER VOICE (SpeechSynthesis / Web Speech API) — browsers do not
      expose the synthesized waveform to the Web Audio graph, so there is
      no legal way to get real frequency data from it. Rather than fake a
      generic repeating animation, we drive a synthetic envelope from the
      utterance's own `boundary` events (fired per word/sentence as it is
      actually spoken) plus a decaying impulse per event. It's an
      approximation, but it's timed to real speech events, not a loop.

   Either way, scene.js only ever calls getFrequencyBands() and gets back
   the same shape of data, so it doesn't need to know which path is active.
   ========================================================================== */

(function (global) {
  "use strict";

  var BIN_COUNT = 64; // matches the 64 outer radial spokes in scene.js

  var audioCtx = null;
  var analyser = null;
  var freqData = null; // Uint8Array, real path
  var sourceNode = null;
  var connectedElement = null;

  // Synthetic path state
  var synthetic = {
    active: false,
    impulses: new Float32Array(BIN_COUNT), // decaying per-band energy
    phase: 0
  };

  var smoothedBands = new Float32Array(BIN_COUNT);

  function ensureContext() {
    if (!audioCtx) {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      audioCtx = new Ctx();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = BIN_COUNT * 2;
      analyser.smoothingTimeConstant = 0.82;
      freqData = new Uint8Array(analyser.frequencyBinCount);
    }
    return audioCtx;
  }

  // Must be called from a user-gesture handler (click/keydown) — browsers
  // suspend AudioContext until then.
  function unlock() {
    var ctx = ensureContext();
    if (ctx && ctx.state === "suspended") {
      ctx.resume().catch(function () {});
    }
  }

  /*
   * Connects a <audio> element to the analyser graph. Safe to call once
   * per element instance (createMediaElementSource throws if called twice
   * on the same element — backend.js always creates a fresh Audio() per
   * response, so this is naturally fine).
   */
  function connectElement(audioEl) {
    var ctx = ensureContext();
    if (!ctx || !audioEl) return false;

    try {
      sourceNode = ctx.createMediaElementSource(audioEl);
      sourceNode.connect(analyser);
      analyser.connect(ctx.destination);
      connectedElement = audioEl;
      synthetic.active = false;
      return true;
    } catch (err) {
      // Already connected, or blocked — fall back silently, backend voice
      // will still play audio, it just won't drive the visualizer.
      console.warn("[JarvisAudio] Could not connect analyser:", err.message);
      return false;
    }
  }

  function beginSynthetic() {
    synthetic.active = true;
    synthetic.impulses.fill(0);
    synthetic.phase = 0;
  }

  function endSynthetic() {
    synthetic.active = false;
  }

  // Call on a SpeechSynthesisUtterance 'boundary' event to inject a pulse
  // of energy that decays naturally over the following frames.
  function pulseSynthetic(strength) {
    var s = typeof strength === "number" ? strength : 1;
    for (var i = 0; i < BIN_COUNT; i++) {
      // Bias energy toward low/mid bands like a voice formant, with a
      // touch of randomness so no two words look identical.
      var band = i / BIN_COUNT;
      var voiceShape = Math.exp(-band * 3.2) * 0.8 + 0.2 * Math.exp(-Math.pow(band - 0.35, 2) * 12);
      var jitter = 0.6 + Math.random() * 0.6;
      synthetic.impulses[i] = Math.min(1, synthetic.impulses[i] + voiceShape * jitter * s);
    }
  }

  /*
   * Returns a Float32Array of length BIN_COUNT, values 0-1, smoothed.
   * This is the single thing scene.js pulls from every frame.
   */
  function getFrequencyBands() {
    if (synthetic.active) {
      synthetic.phase += 0.12;
      for (var i = 0; i < BIN_COUNT; i++) {
        // Decay the impulse and blend in a very soft ambient wobble so
        // silence between words doesn't look completely dead.
        synthetic.impulses[i] *= 0.88;
        var ambient = (Math.sin(synthetic.phase + i * 0.4) * 0.5 + 0.5) * 0.08;
        var target = Math.min(1, synthetic.impulses[i] + ambient);
        smoothedBands[i] += (target - smoothedBands[i]) * 0.35;
      }
      return smoothedBands;
    }

    if (analyser && connectedElement) {
      analyser.getByteFrequencyData(freqData);
      for (var j = 0; j < BIN_COUNT; j++) {
        var idx = Math.floor((j / BIN_COUNT) * freqData.length);
        var target2 = freqData[idx] / 255;
        smoothedBands[j] += (target2 - smoothedBands[j]) * 0.5;
      }
      return smoothedBands;
    }

    // Nothing connected — decay toward silence.
    for (var k = 0; k < BIN_COUNT; k++) {
      smoothedBands[k] *= 0.9;
    }
    return smoothedBands;
  }

  function getOverallAmplitude() {
    var bands = getFrequencyBands();
    var sum = 0;
    for (var i = 0; i < bands.length; i++) sum += bands[i];
    return sum / bands.length;
  }

  function disconnectElement() {
    connectedElement = null;
  }

  global.JarvisAudio = {
    BIN_COUNT: BIN_COUNT,
    unlock: unlock,
    connectElement: connectElement,
    disconnectElement: disconnectElement,
    beginSynthetic: beginSynthetic,
    endSynthetic: endSynthetic,
    pulseSynthetic: pulseSynthetic,
    getFrequencyBands: getFrequencyBands,
    getOverallAmplitude: getOverallAmplitude
  };
})(window);


/* ==========================================================================
   JARVIS SCENE
   --------------------------------------------------------------------------
   All Three.js scene setup and the main animation loop live here. Layers,
   from center outward:

     1. coreHot / coreWireInner / coreWireOuter  - central glowing core
     2. innerRing                                - inner circular geometry
     3. ringA / ringB / ringC                     - concentric orbit rings
     4. radialSpokes                              - outer radial EQ spokes
     5. particlesNear                             - main particle field
     6. outerShell                                - sparse outer energy shell
     7. particlesFar                              - ambient floating dust
     8. glowNear / glowFar (sprites)              - atmospheric glow

   Every layer moves at its own speed/direction so nothing reads as one
   rigid object spinning — see the per-layer constants below.

   Visual intensity is driven by state.js targets, smoothly lerped every
   frame, plus (in SPEAKING) real/synthetic frequency data from audio.js.
   ========================================================================== */

(function (global) {
  "use strict";

  var COLOR_HOT = 0x9df6ff;
  var COLOR_PRIMARY = 0x4fd8ff;
  var COLOR_DIM = 0x1c5f78;

  var SPOKE_COUNT = global.JarvisAudio ? global.JarvisAudio.BIN_COUNT : 64;

  var renderer, scene, camera, clock;
  var canvasEl;
  var rafId = null;
  var isRunning = false;

  var coreGroup, ringsGroup, radialGroup, particleGroupNear, particleGroupFar, outerShell, scanArc;
  var coreHot, coreWireInner, coreWireOuter, glowNear, glowFar;
  var ringA, ringB, ringC, innerRing;

  // Live (lerped) visual values — start at IDLE resting values.
  var live = {
    coreScale: 1.0,
    ringSpeed: 1.0,
    particleSpeed: 1.0,
    radialBase: 0.22,
    radialVariance: 0.06,
    glow: 0.55,
    particleOpacity: 0.35,
    scanline: 0
  };

  // User/settings-controlled multipliers (Settings panel -> Visualization).
  var userIntensity = 1.0;
  var userSpeed = 1.0;
  var userDensity = 1.0;
  var autoDensityOverride = false; // becomes true once user touches the slider

  var elapsed = 0;

  function isMobileViewport() {
    return window.innerWidth < 720;
  }

  /* -------------------------------------------------------------------- */
  /* Texture helpers                                                       */
  /* -------------------------------------------------------------------- */

  function createGlowTexture() {
    var size = 256;
    var canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    var ctx = canvas.getContext("2d");
    var gradient = ctx.createRadialGradient(
      size / 2, size / 2, 0,
      size / 2, size / 2, size / 2
    );
    gradient.addColorStop(0, "rgba(210, 250, 255, 0.9)");
    gradient.addColorStop(0.35, "rgba(79, 216, 255, 0.35)");
    gradient.addColorStop(1, "rgba(79, 216, 255, 0)");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
    var tex = new THREE.CanvasTexture(canvas);
    tex.needsUpdate = true;
    return tex;
  }

  /* -------------------------------------------------------------------- */
  /* Geometry builders                                                     */
  /* -------------------------------------------------------------------- */

  function createRingLine(radius, segments) {
    var positions = new Float32Array((segments + 1) * 3);
    for (var i = 0; i <= segments; i++) {
      var a = (i / segments) * Math.PI * 2;
      positions[i * 3] = Math.cos(a) * radius;
      positions[i * 3 + 1] = Math.sin(a) * radius;
      positions[i * 3 + 2] = 0;
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geo;
  }

  function createRadialSpokes(count) {
    // Each spoke is a line segment: inner point -> outer point.
    var positions = new Float32Array(count * 2 * 3);
    var dirs = new Float32Array(count * 2); // cos, sin per spoke

    for (var i = 0; i < count; i++) {
      var a = (i / count) * Math.PI * 2;
      dirs[i * 2] = Math.cos(a);
      dirs[i * 2 + 1] = Math.sin(a);
    }

    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));

    var mat = new THREE.LineBasicMaterial({
      color: COLOR_PRIMARY,
      transparent: true,
      opacity: 0.75
    });

    var lines = new THREE.LineSegments(geo, mat);
    lines.userData.dirs = dirs;
    lines.userData.count = count;
    // Per-spoke random phase/speed so the idle wobble feels organic, not
    // like a single sine wave stamped around the circle.
    var noisePhase = new Float32Array(count);
    var noiseSpeed = new Float32Array(count);
    for (var j = 0; j < count; j++) {
      noisePhase[j] = Math.random() * Math.PI * 2;
      noiseSpeed[j] = 0.6 + Math.random() * 0.8;
    }
    lines.userData.noisePhase = noisePhase;
    lines.userData.noiseSpeed = noiseSpeed;

    return lines;
  }

  function buildParticleSystem(count, innerR, outerR, baseSize, spread) {
    var positions = new Float32Array(count * 3);
    var data = {
      radius: new Float32Array(count),
      theta: new Float32Array(count),
      phi: new Float32Array(count),
      speed: new Float32Array(count)
    };

    for (var i = 0; i < count; i++) {
      var r = innerR + Math.random() * (outerR - innerR);
      var theta = Math.random() * Math.PI * 2;
      var phi = (Math.random() - 0.5) * spread;

      data.radius[i] = r;
      data.theta[i] = theta;
      data.phi[i] = phi;
      data.speed[i] = 0.15 + Math.random() * 0.35;

      positions[i * 3] = Math.cos(theta) * r;
      positions[i * 3 + 1] = Math.sin(phi) * r * 0.5;
      positions[i * 3 + 2] = Math.sin(theta) * r;
    }

    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));

    var mat = new THREE.PointsMaterial({
      color: COLOR_PRIMARY,
      size: baseSize,
      transparent: true,
      opacity: 0.4,
      sizeAttenuation: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false
    });

    var points = new THREE.Points(geo, mat);
    points.userData.data = data;
    points.userData.count = count;
    return points;
  }

  function disposeObject(obj) {
    if (!obj) return;
    if (obj.geometry) obj.geometry.dispose();
    if (obj.material) {
      if (obj.material.map) obj.material.map.dispose();
      obj.material.dispose();
    }
  }

  /* -------------------------------------------------------------------- */
  /* Scene assembly                                                        */
  /* -------------------------------------------------------------------- */

  function buildCore() {
    coreGroup = new THREE.Group();

    // Atmospheric glow, behind everything, additive.
    var glowTex = createGlowTexture();
    var glowMatFar = new THREE.SpriteMaterial({
      map: glowTex,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      opacity: 0.5
    });
    glowFar = new THREE.Sprite(glowMatFar);
    glowFar.scale.set(9, 9, 1);
    coreGroup.add(glowFar);

    var glowMatNear = new THREE.SpriteMaterial({
      map: glowTex,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      opacity: 0.85
    });
    glowNear = new THREE.Sprite(glowMatNear);
    glowNear.scale.set(3.4, 3.4, 1);
    coreGroup.add(glowNear);

    // Bright hot center.
    var hotGeo = new THREE.SphereGeometry(0.4, 24, 24);
    var hotMat = new THREE.MeshBasicMaterial({
      color: COLOR_HOT,
      transparent: true,
      opacity: 0.95
    });
    coreHot = new THREE.Mesh(hotGeo, hotMat);
    coreGroup.add(coreHot);

    // Layered wireframe geometry around the hot center.
    var wireGeoInner = new THREE.IcosahedronGeometry(0.72, 1);
    var wireMatInner = new THREE.MeshBasicMaterial({
      color: COLOR_PRIMARY,
      wireframe: true,
      transparent: true,
      opacity: 0.55
    });
    coreWireInner = new THREE.Mesh(wireGeoInner, wireMatInner);
    coreGroup.add(coreWireInner);

    var wireGeoOuter = new THREE.IcosahedronGeometry(1.0, 1);
    var wireMatOuter = new THREE.MeshBasicMaterial({
      color: COLOR_DIM,
      wireframe: true,
      transparent: true,
      opacity: 0.35
    });
    coreWireOuter = new THREE.Mesh(wireGeoOuter, wireMatOuter);
    coreGroup.add(coreWireOuter);

    // Inner circular geometry — a thin flat annulus, distinct from the
    // orbiting line-rings added below.
    var innerRingGeo = new THREE.RingGeometry(1.18, 1.24, 64);
    var innerRingMat = new THREE.MeshBasicMaterial({
      color: COLOR_PRIMARY,
      transparent: true,
      opacity: 0.3,
      side: THREE.DoubleSide
    });
    innerRing = new THREE.Mesh(innerRingGeo, innerRingMat);
    coreGroup.add(innerRing);

    return coreGroup;
  }

  function buildRings() {
    ringsGroup = new THREE.Group();

    var specs = [
      { radius: 1.6, tiltX: 1.35, tiltZ: 0.08, opacity: 0.55, speed: 0.35, dir: 1 },
      { radius: 2.15, tiltX: 1.45, tiltZ: -0.14, opacity: 0.4, speed: 0.22, dir: -1 },
      { radius: 2.75, tiltX: 1.3, tiltZ: 0.2, opacity: 0.25, speed: 0.15, dir: 1 }
    ];

    var rings = [];
    for (var i = 0; i < specs.length; i++) {
      var s = specs[i];
      var geo = createRingLine(s.radius, 128);
      var mat = new THREE.LineBasicMaterial({
        color: COLOR_PRIMARY,
        transparent: true,
        opacity: s.opacity
      });
      var ring = new THREE.LineLoop(geo, mat);
      ring.rotation.x = s.tiltX;
      ring.rotation.z = s.tiltZ;
      ring.userData.speed = s.speed;
      ring.userData.dir = s.dir;
      ring.userData.baseOpacity = s.opacity;
      ringsGroup.add(ring);
      rings.push(ring);
    }
    ringA = rings[0];
    ringB = rings[1];
    ringC = rings[2];

    // Thin scanning arc used during THINKING — a short bright segment
    // that sweeps quickly around ringA's radius.
    var arcSegments = 10;
    var arcSpan = 0.35; // radians
    var arcPositions = new Float32Array((arcSegments + 1) * 3);
    for (var a = 0; a <= arcSegments; a++) {
      var t = (a / arcSegments) * arcSpan;
      arcPositions[a * 3] = Math.cos(t) * 1.6;
      arcPositions[a * 3 + 1] = Math.sin(t) * 1.6;
      arcPositions[a * 3 + 2] = 0;
    }
    var arcGeo = new THREE.BufferGeometry();
    arcGeo.setAttribute("position", new THREE.BufferAttribute(arcPositions, 3));
    var arcMat = new THREE.LineBasicMaterial({
      color: COLOR_HOT,
      transparent: true,
      opacity: 0
    });
    scanArc = new THREE.Line(arcGeo, arcMat);
    scanArc.rotation.x = specs[0].tiltX;
    scanArc.rotation.z = specs[0].tiltZ;
    ringsGroup.add(scanArc);

    return ringsGroup;
  }

  function buildOuterShell() {
    var geo = new THREE.IcosahedronGeometry(4.4, 0);
    var mat = new THREE.MeshBasicMaterial({
      color: COLOR_DIM,
      wireframe: true,
      transparent: true,
      opacity: 0.1
    });
    outerShell = new THREE.Mesh(geo, mat);
    return outerShell;
  }

  function baseParticleCount(tier) {
    var mobile = isMobileViewport();
    if (tier === "near") return mobile ? 350 : 900;
    return mobile ? 120 : 300;
  }

  function rebuildParticles() {
    if (particleGroupNear) {
      scene.remove(particleGroupNear);
      disposeObject(particleGroupNear);
    }
    if (particleGroupFar) {
      scene.remove(particleGroupFar);
      disposeObject(particleGroupFar);
    }

    var densityMultiplier = userDensity;
    var nearCount = Math.max(40, Math.round(baseParticleCount("near") * densityMultiplier));
    var farCount = Math.max(20, Math.round(baseParticleCount("far") * densityMultiplier));

    particleGroupNear = buildParticleSystem(nearCount, 1.9, 4.0, 0.055, 1.4);
    particleGroupFar = buildParticleSystem(farCount, 4.2, 6.5, 0.09, 2.2);
    particleGroupFar.material.opacity = 0.18;

    scene.add(particleGroupNear);
    scene.add(particleGroupFar);
  }

  /* -------------------------------------------------------------------- */
  /* Init / resize                                                         */
  /* -------------------------------------------------------------------- */

  function init(canvas) {
    canvasEl = canvas;
    clock = new THREE.Clock();

    scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x04070a, 0.045);

    camera = new THREE.PerspectiveCamera(
      50,
      window.innerWidth / window.innerHeight,
      0.1,
      100
    );
    camera.position.set(0, 0, 7.2);
    camera.lookAt(0, 0, 0);

    renderer = new THREE.WebGLRenderer({
      canvas: canvasEl,
      antialias: true,
      alpha: true,
      powerPreference: "high-performance"
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);

    scene.add(buildCore());
    scene.add(buildRings());
    radialGroup = createRadialSpokes(SPOKE_COUNT);
    scene.add(radialGroup);
    scene.add(buildOuterShell());
    rebuildParticles();

    applyResponsiveScale();

    window.addEventListener("resize", onResize, { passive: true });
    document.addEventListener("visibilitychange", onVisibilityChange);

    start();
  }

  function onResize() {
    if (!renderer) return;
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    applyResponsiveScale();

    if (!autoDensityOverride) {
      userDensity = isMobileViewport() ? 0.6 : 1.0;
      rebuildParticles();
    }
  }

  function applyResponsiveScale() {
    var mobile = isMobileViewport();
    var scale = mobile ? 0.68 : 1.0;
    coreGroup.scale.setScalar(scale);
    ringsGroup.scale.setScalar(scale);
    radialGroup.scale.setScalar(scale);
    outerShell.scale.setScalar(scale);
    camera.position.z = mobile ? 8.4 : 7.2;
  }

  function onVisibilityChange() {
    if (document.hidden) {
      stop();
    } else {
      start();
    }
  }

  function start() {
    if (isRunning) return;
    isRunning = true;
    clock.getDelta(); // discard stale delta from time spent paused
    rafId = requestAnimationFrame(animate);
  }

  function stop() {
    isRunning = false;
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
  }

  /* -------------------------------------------------------------------- */
  /* Per-frame update                                                      */
  /* -------------------------------------------------------------------- */

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function updateLiveTargets(dt) {
    var target = global.JarvisState.getVisualTargets();
    var smoothing = 1 - Math.pow(0.001, dt); // frame-rate independent lerp

    live.coreScale = lerp(live.coreScale, target.coreScale, smoothing);
    live.ringSpeed = lerp(live.ringSpeed, target.ringSpeed, smoothing);
    live.particleSpeed = lerp(live.particleSpeed, target.particleSpeed, smoothing);
    live.radialBase = lerp(live.radialBase, target.radialBase, smoothing);
    live.radialVariance = lerp(live.radialVariance, target.radialVariance, smoothing);
    live.glow = lerp(live.glow, target.glow, smoothing);
    live.particleOpacity = lerp(live.particleOpacity, target.particleOpacity, smoothing);
    live.scanline = lerp(live.scanline, target.scanline ? 1 : 0, smoothing);
    live.audioReactive = target.audioReactive;
  }

  function updateCore(dt) {
    var pulse = Math.sin(elapsed * 1.6) * global.JarvisState.getVisualTargets().corePulse;
    var scale = (live.coreScale + pulse) * userIntensity;
    coreHot.scale.setScalar(scale);
    coreWireInner.scale.setScalar(scale * 1.02);
    coreWireOuter.scale.setScalar(scale * 1.04);
    innerRing.scale.setScalar(scale);

    coreWireInner.rotation.y += dt * 0.5 * userSpeed;
    coreWireInner.rotation.x += dt * 0.22 * userSpeed;
    coreWireOuter.rotation.y -= dt * 0.28 * userSpeed;
    coreWireOuter.rotation.z += dt * 0.16 * userSpeed;
    innerRing.rotation.z += dt * 0.3 * userSpeed;

    var glowScale = 1 + live.glow * 0.5 * userIntensity;
    glowNear.scale.set(3.4 * glowScale, 3.4 * glowScale, 1);
    glowNear.material.opacity = 0.55 + live.glow * 0.4;
    glowFar.material.opacity = 0.3 + live.glow * 0.3;

    coreHot.material.opacity = 0.75 + live.glow * 0.25;
  }

  function updateRings(dt) {
    var rings = [ringA, ringB, ringC];
    for (var i = 0; i < rings.length; i++) {
      var r = rings[i];
      r.rotation.y += dt * r.userData.speed * r.userData.dir * live.ringSpeed * userSpeed;
      r.material.opacity = r.userData.baseOpacity * (0.6 + live.glow * 0.4);
    }

    // Scan sweep: fast constant rotation, only visible during THINKING.
    scanArc.rotation.y += dt * 3.4 * userSpeed;
    scanArc.material.opacity = live.scanline * 0.9;
  }

  function updateRadialSpokes(dt) {
    var pos = radialGroup.geometry.attributes.position.array;
    var dirs = radialGroup.userData.dirs;
    var count = radialGroup.userData.count;
    var noisePhase = radialGroup.userData.noisePhase;
    var noiseSpeed = radialGroup.userData.noiseSpeed;

    var innerR = 1.3;
    var maxExtra = 2.3 * userIntensity;

    var bands = live.audioReactive && global.JarvisAudio
      ? global.JarvisAudio.getFrequencyBands()
      : null;

    for (var i = 0; i < count; i++) {
      var dx = dirs[i * 2];
      var dy = dirs[i * 2 + 1];

      var organic = Math.sin(elapsed * noiseSpeed[i] + noisePhase[i]) * 0.5 + 0.5;
      var audioValue = bands ? bands[i] : 0;

      // Blend the organic idle wobble with real/synthetic audio energy —
      // audio dominates once it has meaningful signal, otherwise the
      // spoke falls back to the ambient organic motion.
      var energy = live.audioReactive
        ? lerp(organic * 0.4, audioValue, 0.75)
        : organic;

      var length = live.radialBase + energy * (live.radialVariance + maxExtra * (live.audioReactive ? 1 : 0.15));
      length = Math.max(0.02, length);

      var outerR = innerR + length * 2.4;

      var idx = i * 2 * 3;
      pos[idx] = dx * innerR;
      pos[idx + 1] = dy * innerR;
      pos[idx + 2] = 0;
      pos[idx + 3] = dx * outerR;
      pos[idx + 4] = dy * outerR;
      pos[idx + 5] = 0;
    }

    radialGroup.geometry.attributes.position.needsUpdate = true;
    radialGroup.material.opacity = 0.5 + live.glow * 0.4;
    radialGroup.rotation.z += dt * 0.05 * userSpeed;
  }

  function updateParticles(dt, group, spinDir) {
    var data = group.userData.data;
    var count = group.userData.count;
    var pos = group.geometry.attributes.position.array;
    var amp = global.JarvisAudio ? global.JarvisAudio.getOverallAmplitude() : 0;
    var speedBoost = live.audioReactive ? 1 + amp * 1.5 : 1;

    for (var i = 0; i < count; i++) {
      data.theta[i] += dt * data.speed[i] * live.particleSpeed * userSpeed * spinDir * speedBoost;
      var r = data.radius[i];
      var idx = i * 3;
      pos[idx] = Math.cos(data.theta[i]) * r;
      pos[idx + 1] = Math.sin(data.phi[i]) * r * 0.5;
      pos[idx + 2] = Math.sin(data.theta[i]) * r;
    }
    group.geometry.attributes.position.needsUpdate = true;
    group.material.opacity = (group === particleGroupNear ? live.particleOpacity : live.particleOpacity * 0.4) * userIntensity;
    group.rotation.y += dt * 0.03 * spinDir * userSpeed;
  }

  function updateOuterShell(dt) {
    outerShell.rotation.y += dt * 0.04 * userSpeed;
    outerShell.rotation.x += dt * 0.015 * userSpeed;
    outerShell.material.opacity = 0.06 + live.glow * 0.08;
  }

  function updateCamera(dt) {
    // Very subtle drift so the whole scene feels alive, not static-camera.
    camera.position.x = Math.sin(elapsed * 0.12) * 0.18;
    camera.position.y = Math.cos(elapsed * 0.09) * 0.12;
    camera.lookAt(0, 0, 0);
  }

  function animate() {
    if (!isRunning) return;
    var dt = Math.min(clock.getDelta(), 0.1);
    elapsed += dt;

    updateLiveTargets(dt);
    updateCore(dt);
    updateRings(dt);
    updateRadialSpokes(dt);
    updateParticles(dt, particleGroupNear, 1);
    updateParticles(dt, particleGroupFar, -1);
    updateOuterShell(dt);
    updateCamera(dt);

    renderer.render(scene, camera);
    rafId = requestAnimationFrame(animate);
  }

  /* -------------------------------------------------------------------- */
  /* Settings hooks (Visualization panel)                                  */
  /* -------------------------------------------------------------------- */

  function setIntensity(value) {
    userIntensity = Math.max(0.3, Math.min(2, value));
  }

  function setAnimationSpeed(value) {
    userSpeed = Math.max(0.2, Math.min(2.5, value));
  }

  function setParticleDensity(value) {
    autoDensityOverride = true;
    userDensity = Math.max(0.2, Math.min(2, value));
    rebuildParticles();
  }

  global.JarvisScene = {
    init: init,
    setIntensity: setIntensity,
    setAnimationSpeed: setAnimationSpeed,
    setParticleDensity: setParticleDensity
  };
})(window);


/* ==========================================================================
   JARVIS BACKEND COMMUNICATION
   --------------------------------------------------------------------------
   Talks to the existing local FastAPI backend. Nothing about the backend
   itself changes here — same base URL, same endpoints, same request/
   response shapes as the original script.js. This module just wraps them
   cleanly and tracks reachability for the Settings > System panel.
   ========================================================================== */

(function (global) {
  "use strict";

  var BACKEND_URL = "http://127.0.0.1:8000";

  var lastBackendReachable = null; // null = unknown, true/false once checked
  var statusListeners = [];

  function notifyStatus(reachable) {
    lastBackendReachable = reachable;
    for (var i = 0; i < statusListeners.length; i++) {
      try {
        statusListeners[i](reachable);
      } catch (err) {
        console.error("[JarvisBackend] status listener error:", err);
      }
    }
  }

  /*
   * POST /chat  { question } -> { answer }
   */
  async function sendChatMessage(question) {
    var response = await fetch(BACKEND_URL + "/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question })
    });

    if (!response.ok) {
      notifyStatus(false);
      throw new Error("Backend responded with status " + response.status);
    }

    var data = await response.json();
    notifyStatus(true);
    return data.answer;
  }

  /*
   * POST /speak  { text } -> { audio_base64, audio_format }
   */
  async function fetchBackendSpeech(text) {
    var response = await fetch(BACKEND_URL + "/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text })
    });

    var data = await response.json();
    if (data.error) throw new Error(data.error);

    notifyStatus(true);
    return data; // { audio_base64, audio_format }
  }

  /*
   * Lightweight reachability probe for the Settings panel. There's no
   * dedicated /health endpoint on the backend, so we just confirm the
   * server accepts a connection — any HTTP response (even a 404) means
   * it's reachable; a network failure means it isn't.
   */
  async function checkHealth() {
    try {
      var controller = new AbortController();
      var timeout = setTimeout(function () { controller.abort(); }, 2500);
      await fetch(BACKEND_URL + "/", { method: "GET", signal: controller.signal });
      clearTimeout(timeout);
      notifyStatus(true);
      return true;
    } catch (err) {
      notifyStatus(false);
      return false;
    }
  }

  function onStatusChange(callback) {
    if (typeof callback === "function") statusListeners.push(callback);
  }

  function getLastKnownStatus() {
    return lastBackendReachable;
  }

  global.JarvisBackend = {
    BACKEND_URL: BACKEND_URL,
    sendChatMessage: sendChatMessage,
    fetchBackendSpeech: fetchBackendSpeech,
    checkHealth: checkHealth,
    onStatusChange: onStatusChange,
    getLastKnownStatus: getLastKnownStatus
  };
})(window);


/* ==========================================================================
   JARVIS VOICE
   --------------------------------------------------------------------------
   Two text-to-speech paths, same as the original script.js:

     - Web Speech API (instant, zero backend calls, default)
     - Backend /speak (pyttsx3, fully offline, higher effort)

   speak() is the single entry point: it puts the state machine into
   SPEAKING and picks whichever path is active. Each path is responsible
   for putting the state machine back to IDLE when the audio actually
   finishes (not just when the fetch resolves).
   ========================================================================== */

(function (global) {
  "use strict";

  var PREFERRED_VOICE_NAMES = [
    "Google UK English Male",
    "Microsoft Ryan Online (Natural) - English (United Kingdom)",
    "Microsoft George - English (United Kingdom)",
    "Daniel",
    "Arthur"
  ];

  var voiceEnabled = true;
  var useBackendVoice = false;
  var rate = 0.95;
  var volume = 1.0;
  var pitch = 0.85;
  var jarvisVoice = null;
  var currentAudio = null;
  var voicesReadyCallbacks = [];
  var audioUnlocked = false;

  function cleanForSpeech(text) {
    return String(text || "")
      .replace(/\*\*/g, "")
      .replace(/\*/g, "")
      .replace(/`/g, "")
      .replace(/#/g, "")
      .trim();
  }

  function pickPreferredVoice(voices) {
    for (var i = 0; i < PREFERRED_VOICE_NAMES.length; i++) {
      var match = voices.find(function (v) { return v.name === PREFERRED_VOICE_NAMES[i]; });
      if (match) return match;
    }
    return (
      voices.find(function (v) { return v.lang === "en-GB" && /male|daniel|george|ryan/i.test(v.name); }) ||
      voices.find(function (v) { return v.lang === "en-GB"; }) ||
      voices[0] ||
      null
    );
  }

  function initVoices() {
    if (!window.speechSynthesis) return;
    var voices = window.speechSynthesis.getVoices();
    if (!voices.length) return;

    jarvisVoice = pickPreferredVoice(voices);

    voicesReadyCallbacks.forEach(function (cb) {
      try { cb(voices); } catch (err) { console.error(err); }
    });
  }

  if (window.speechSynthesis) {
    window.speechSynthesis.onvoiceschanged = initVoices;
    initVoices();
  }

  /* -------------------------------------------------------------------- */
  /* Path 1: Web Speech API                                                */
  /* -------------------------------------------------------------------- */

  function speakViaWebSpeech(text) {
    if (!window.speechSynthesis || !text) {
      global.JarvisState.set("IDLE");
      return;
    }

    window.speechSynthesis.cancel();
    global.JarvisAudio.beginSynthetic();

    var utterance = new SpeechSynthesisUtterance(text);
    if (jarvisVoice) utterance.voice = jarvisVoice;
    utterance.pitch = pitch;
    utterance.rate = rate;
    utterance.volume = volume;
    utterance.lang = "en-GB";

    utterance.onstart = function () {
      global.JarvisAudio.pulseSynthetic(0.8);
    };
    utterance.onboundary = function () {
      global.JarvisAudio.pulseSynthetic(1);
    };
    utterance.onend = function () {
      global.JarvisAudio.endSynthetic();
      global.JarvisState.set("IDLE");
    };
    utterance.onerror = function (e) {
      console.warn("Speech synthesis error:", e.error);
      global.JarvisAudio.endSynthetic();
      global.JarvisState.set("IDLE");
    };

    window.speechSynthesis.speak(utterance);
  }

  /* -------------------------------------------------------------------- */
  /* Path 2: Backend voice (/speak, pyttsx3)                               */
  /* -------------------------------------------------------------------- */

  async function speakViaBackend(text) {
    if (currentAudio) {
      currentAudio.pause();
      currentAudio.currentTime = 0;
    }

    try {
      var data = await global.JarvisBackend.fetchBackendSpeech(text);
      var audioSrc = "data:audio/" + data.audio_format + ";base64," + data.audio_base64;
      currentAudio = new Audio(audioSrc);
      currentAudio.volume = volume;

      global.JarvisAudio.connectElement(currentAudio);

      currentAudio.onended = function () {
        global.JarvisAudio.disconnectElement();
        global.JarvisState.set("IDLE");
      };
      currentAudio.onerror = function () {
        global.JarvisAudio.disconnectElement();
        global.JarvisState.set("IDLE");
      };

      await currentAudio.play().catch(function (err) {
        // Autoplay blocked until first user gesture — expected on first load.
        console.warn("Autoplay blocked, will resume after user interaction:", err);
        global.JarvisState.set("IDLE");
      });
    } catch (err) {
      console.error("Backend TTS request failed, falling back to Web Speech:", err);
      speakViaWebSpeech(text);
    }
  }

  /* -------------------------------------------------------------------- */
  /* Public dispatcher + config                                            */
  /* -------------------------------------------------------------------- */

  function speak(text) {
    if (!voiceEnabled || !text || !text.trim()) return;
    global.JarvisState.set("SPEAKING");

    if (useBackendVoice) {
      speakViaBackend(text);
    } else {
      speakViaWebSpeech(text);
    }
  }

  function stop() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    if (currentAudio) currentAudio.pause();
    global.JarvisAudio.endSynthetic();
    global.JarvisAudio.disconnectElement();
  }

  function setEnabled(enabled) {
    voiceEnabled = !!enabled;
    if (!voiceEnabled) stop();
  }

  function setUseBackendVoice(enabled) {
    useBackendVoice = !!enabled;
  }

  function setRate(value) {
    rate = value;
  }

  function setVolume(value) {
    volume = value;
    if (currentAudio) currentAudio.volume = value;
  }

  function setVoiceByName(name) {
    if (!window.speechSynthesis) return;
    var voices = window.speechSynthesis.getVoices();
    var match = voices.find(function (v) { return v.name === name; });
    if (match) jarvisVoice = match;
  }

  function onVoicesReady(callback) {
    voicesReadyCallbacks.push(callback);
    if (window.speechSynthesis && window.speechSynthesis.getVoices().length) {
      callback(window.speechSynthesis.getVoices());
    }
  }

  // Browsers block audio (both SpeechSynthesis and Web Audio) until a real
  // user gesture. Call this from the first click/keydown on the page.
  function unlock() {
    if (audioUnlocked) return;
    audioUnlocked = true;

    if (window.speechSynthesis) {
      var primer = new SpeechSynthesisUtterance(" ");
      primer.volume = 0;
      window.speechSynthesis.speak(primer);
    }
    global.JarvisAudio.unlock();
  }

  global.JarvisVoice = {
    speak: speak,
    stop: stop,
    cleanForSpeech: cleanForSpeech,
    setEnabled: setEnabled,
    setUseBackendVoice: setUseBackendVoice,
    setRate: setRate,
    setVolume: setVolume,
    setVoiceByName: setVoiceByName,
    onVoicesReady: onVoicesReady,
    unlock: unlock
  };
})(window);


/* ==========================================================================
   JARVIS UI
   --------------------------------------------------------------------------
   Everything that isn't the 3D scene: the dev transcript readout, the
   sliding History panel, the sliding Settings panel, and the status label.
   Talks to JarvisScene / JarvisVoice / JarvisBackend through their public
   APIs only — no direct reach-into-internals.
   ========================================================================== */

(function (global) {
  "use strict";

  // One flag, one place — per the brief, the dev transcript readout can be
  // switched off entirely by flipping this to false (or by adding the
  // "transcript-hidden" class to #transcript-panel directly in the HTML).
  var CONFIG = {
    showTranscript: true
  };

  var els = {};
  var historyLog = []; // { role, text, timestamp }
  var settingsHealthTimer = null;

  function $(id) {
    return document.getElementById(id);
  }

  function cacheElements() {
    els.stateLabel = $("state-label");
    els.body = document.body;

    els.transcriptPanel = $("transcript-panel");
    els.transcriptYou = $("transcript-you");
    els.transcriptJarvis = $("transcript-jarvis");

    els.historyBtn = $("historyBtn");
    els.historyPanel = $("history-panel");
    els.historyList = $("history-list");

    els.settingsBtn = $("settingsBtn");
    els.settingsPanel = $("settings-panel");

    els.overlay = $("panel-overlay");

    els.micStatusText = $("system-mic-status");
    els.backendStatusDot = $("system-backend-dot");
    els.backendStatusText = $("system-backend-status");
    els.ollamaStatusText = $("system-ollama-status");

    els.topbarStatusDot = $("topbar-status-dot");
    els.topbarStatusText = $("topbar-status-text");
  }

  function updateTopbarStatus(reachable) {
    if (els.topbarStatusDot) {
      els.topbarStatusDot.classList.toggle("status-dot--ok", reachable === true);
      els.topbarStatusDot.classList.toggle("status-dot--fail", reachable === false);
    }
    if (els.topbarStatusText) {
      els.topbarStatusText.textContent = reachable === false ? "BACKEND OFFLINE" : "SYSTEM ONLINE";
    }
  }

  /* -------------------------------------------------------------------- */
  /* Status label                                                          */
  /* -------------------------------------------------------------------- */

  function applyStateToDom(state) {
    els.body.setAttribute("data-jarvis-state", state);
    if (els.stateLabel) els.stateLabel.textContent = state;
  }

  /* -------------------------------------------------------------------- */
  /* Transcript (dev panel)                                                */
  /* -------------------------------------------------------------------- */

  function applyTranscriptVisibility() {
    if (!els.transcriptPanel) return;
    els.transcriptPanel.classList.toggle("transcript-hidden", !CONFIG.showTranscript);
  }

  function setTranscriptEnabled(enabled) {
    CONFIG.showTranscript = !!enabled;
    applyTranscriptVisibility();
  }

  function showTranscriptLine(role, text) {
    if (!CONFIG.showTranscript) return;
    var target = role === "user" ? els.transcriptYou : els.transcriptJarvis;
    if (!target) return;
    target.textContent = text;
    target.parentElement.classList.remove("transcript-flash");
    // eslint-disable-next-line no-unused-expressions
    void target.parentElement.offsetWidth; // restart CSS animation
    target.parentElement.classList.add("transcript-flash");
  }

  /* -------------------------------------------------------------------- */
  /* History panel                                                         */
  /* -------------------------------------------------------------------- */

  function formatTime(date) {
    var h = date.getHours();
    var m = date.getMinutes();
    var period = h >= 12 ? "PM" : "AM";
    h = h % 12 || 12;
    return (h < 10 ? "0" + h : h) + ":" + (m < 10 ? "0" + m : m) + " " + period;
  }

  function addHistoryEntry(role, text) {
    var timestamp = new Date();
    historyLog.push({ role: role, text: text, timestamp: timestamp });

    if (!els.historyList) return;
    var entry = document.createElement("div");
    entry.className = "history-entry history-entry--" + role;

    var meta = document.createElement("div");
    meta.className = "history-entry__meta";
    meta.textContent = (role === "user" ? "YOU" : "JARVIS") + " · " + formatTime(timestamp);

    var body = document.createElement("div");
    body.className = "history-entry__text";
    body.textContent = text;

    entry.appendChild(meta);
    entry.appendChild(body);
    els.historyList.appendChild(entry);
    els.historyList.scrollTop = els.historyList.scrollHeight;
  }

  function recordUserMessage(text) {
    showTranscriptLine("user", text);
    addHistoryEntry("user", text);
  }

  function recordAssistantMessage(text) {
    showTranscriptLine("jarvis", text);
    addHistoryEntry("jarvis", text);
  }

  /* -------------------------------------------------------------------- */
  /* Sliding panels (History / Settings)                                   */
  /* -------------------------------------------------------------------- */

  function openPanel(panelEl) {
    closePanels();
    if (!panelEl) return;
    panelEl.classList.add("open");
    panelEl.setAttribute("aria-hidden", "false");
    els.overlay.classList.add("open");

    if (panelEl === els.settingsPanel) {
      refreshSystemStatus();
      settingsHealthTimer = setInterval(refreshSystemStatus, 15000);
    }
  }

  function closePanels() {
    [els.historyPanel, els.settingsPanel].forEach(function (panel) {
      if (!panel) return;
      panel.classList.remove("open");
      panel.setAttribute("aria-hidden", "true");
    });
    if (els.overlay) els.overlay.classList.remove("open");
    if (settingsHealthTimer) {
      clearInterval(settingsHealthTimer);
      settingsHealthTimer = null;
    }
  }

  function toggleHistoryPanel() {
    if (els.historyPanel.classList.contains("open")) {
      closePanels();
    } else {
      openPanel(els.historyPanel);
    }
  }

  function toggleSettingsPanel() {
    if (els.settingsPanel.classList.contains("open")) {
      closePanels();
    } else {
      openPanel(els.settingsPanel);
    }
  }

  /* -------------------------------------------------------------------- */
  /* Settings > System status                                              */
  /* -------------------------------------------------------------------- */

  function refreshSystemStatus() {
    if (els.micStatusText) {
      var supported = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
      els.micStatusText.textContent = supported ? "Available (browser STT)" : "Not supported in this browser";
    }

    if (els.backendStatusDot && els.backendStatusText) {
      els.backendStatusText.textContent = "Checking...";
      global.JarvisBackend.checkHealth().then(function (ok) {
        els.backendStatusDot.classList.toggle("status-dot--ok", ok);
        els.backendStatusDot.classList.toggle("status-dot--fail", !ok);
        els.backendStatusText.textContent = ok
          ? "Connected · " + global.JarvisBackend.BACKEND_URL
          : "Unreachable · " + global.JarvisBackend.BACKEND_URL;

        if (els.ollamaStatusText) {
          els.ollamaStatusText.textContent = ok
            ? "Reported via backend"
            : "Unknown (backend unreachable)";
        }
      });
    }
  }

  /* -------------------------------------------------------------------- */
  /* Settings > Visualization controls                                     */
  /* -------------------------------------------------------------------- */

  function wireRangeInput(id, onInput, initial) {
    var el = $(id);
    if (!el) return;
    if (initial !== undefined) el.value = initial;
    el.addEventListener("input", function () {
      onInput(parseFloat(el.value));
    });
  }

  function wireToggle(id, onChange, initial) {
    var el = $(id);
    if (!el) return;
    el.checked = !!initial;
    el.addEventListener("change", function () {
      onChange(el.checked);
    });
  }

  function wireVisualizationControls() {
    wireRangeInput("setting-intensity", function (v) {
      global.JarvisScene.setIntensity(v);
    }, 1);

    wireRangeInput("setting-particle-density", function (v) {
      global.JarvisScene.setParticleDensity(v);
    }, 1);

    wireRangeInput("setting-animation-speed", function (v) {
      global.JarvisScene.setAnimationSpeed(v);
    }, 1);
  }

  function wireVoiceControls() {
    wireToggle("setting-voice-enabled", function (checked) {
      global.JarvisVoice.setEnabled(checked);
    }, true);

    wireToggle("setting-backend-voice", function (checked) {
      global.JarvisVoice.setUseBackendVoice(checked);
    }, false);

    wireRangeInput("setting-voice-speed", function (v) {
      global.JarvisVoice.setRate(v);
    }, 0.95);

    wireRangeInput("setting-voice-volume", function (v) {
      global.JarvisVoice.setVolume(v);
    }, 1);

    var select = $("setting-voice-select");
    if (select) {
      select.addEventListener("change", function () {
        global.JarvisVoice.setVoiceByName(select.value);
      });
      global.JarvisVoice.onVoicesReady(function (voices) {
        select.innerHTML = "";
        voices.forEach(function (v) {
          var opt = document.createElement("option");
          opt.value = v.name;
          opt.textContent = v.name + " (" + v.lang + ")";
          select.appendChild(opt);
        });
      });
    }
  }

  function wireTranscriptToggle() {
    wireToggle("setting-show-transcript", function (checked) {
      setTranscriptEnabled(checked);
    }, CONFIG.showTranscript);
  }

  /* -------------------------------------------------------------------- */
  /* Init                                                                  */
  /* -------------------------------------------------------------------- */

  function initUI() {
    cacheElements();
    applyTranscriptVisibility();

    global.JarvisState.onChange(applyStateToDom);
    applyStateToDom(global.JarvisState.get());

    if (els.historyBtn) els.historyBtn.addEventListener("click", toggleHistoryPanel);
    if (els.settingsBtn) els.settingsBtn.addEventListener("click", toggleSettingsPanel);
    if (els.overlay) els.overlay.addEventListener("click", closePanels);

    document.querySelectorAll("[data-close]").forEach(function (btn) {
      btn.addEventListener("click", closePanels);
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closePanels();
    });

    wireVisualizationControls();
    wireVoiceControls();
    wireTranscriptToggle();

    global.JarvisBackend.onStatusChange(updateTopbarStatus);
    global.JarvisBackend.checkHealth(); // initial probe, fire-and-forget
  }

  global.JarvisUI = {
    CONFIG: CONFIG,
    initUI: initUI,
    recordUserMessage: recordUserMessage,
    recordAssistantMessage: recordAssistantMessage,
    setTranscriptEnabled: setTranscriptEnabled
  };
})(window);


/* ==========================================================================
   JARVIS MAIN
   --------------------------------------------------------------------------
   Wires everything together: speech-to-text input, the ask/answer round
   trip against the backend, and the buttons in the dock. This is the only
   file that knows about all the other modules — state, scene, audio,
   backend, voice, ui — none of them know about each other directly.
   ========================================================================== */

(function () {
  "use strict";

  var recognition = null;
  var isListening = false;
  var sttResultHandled = false;

  var questionInput, micBtn, askBtn;

  /* -------------------------------------------------------------------- */
  /* Speech recognition (browser STT) — dev/testing input path.            */
  /* The native "Hey Jarvis" / faster-whisper pipeline is the real one;    */
  /* this just gives the frontend something to demo with a mouse + mic.    */
  /* -------------------------------------------------------------------- */

  function setupRecognition() {
    var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      if (micBtn) {
        micBtn.disabled = true;
        micBtn.classList.add("disabled");
        micBtn.title = "Speech recognition not supported in this browser";
      }
      return;
    }

    recognition = new SpeechRecognition();
    recognition.lang = "en-IN";
    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onstart = function () {
      isListening = true;
      sttResultHandled = false;
      micBtn.classList.add("recording");
      questionInput.disabled = true;
      JarvisState.set("LISTENING");
    };

    recognition.onend = function () {
      isListening = false;
      micBtn.classList.remove("recording");
      questionInput.disabled = false;
      if (!sttResultHandled) {
        JarvisState.set("IDLE");
      }
    };

    recognition.onerror = function (event) {
      console.error("SpeechRecognition error:", event.error);
      isListening = false;
      micBtn.classList.remove("recording");
      questionInput.disabled = false;
      JarvisState.set("IDLE");
    };

    recognition.onresult = function (event) {
      var transcript = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      questionInput.value = transcript;

      var last = event.results[event.results.length - 1];
      if (last.isFinal && transcript.trim() !== "") {
        sttResultHandled = true;
        askQuestion();
      }
    };
  }

  function toggleListening() {
    if (!recognition) return;
    if (!isListening) {
      recognition.start();
    } else {
      recognition.stop();
    }
  }

  /* -------------------------------------------------------------------- */
  /* Ask / answer round trip                                               */
  /* -------------------------------------------------------------------- */

  async function askQuestion() {
    var question = questionInput.value.trim();
    if (!question) return;

    JarvisUI.recordUserMessage(question);
    questionInput.value = "";

    JarvisState.set("THINKING");

    try {
      var answer = await JarvisBackend.sendChatMessage(question);
      JarvisUI.recordAssistantMessage(answer);
      JarvisVoice.speak(JarvisVoice.cleanForSpeech(answer));
    } catch (err) {
      console.error("Chat request failed:", err);
      JarvisUI.recordAssistantMessage("I couldn't reach the backend just now.");
      JarvisState.set("IDLE");
    }
  }

  /* -------------------------------------------------------------------- */
  /* Autoplay / AudioContext unlock                                        */
  /* -------------------------------------------------------------------- */

  function unlockAudioOnce() {
    JarvisVoice.unlock();
  }

  /* -------------------------------------------------------------------- */
  /* Init                                                                  */
  /* -------------------------------------------------------------------- */

  function init() {
    questionInput = document.getElementById("question");
    micBtn = document.getElementById("micBtn");
    askBtn = document.getElementById("askBtn");

    // Scene init and UI wiring are independent — if Three.js fails to load
    // or throws for any reason, the buttons/inputs below must still work.
    // A silent visualization failure should never take down the whole app.
    var canvas = document.getElementById("jarvis-canvas");
    try {
      JarvisScene.init(canvas);
    } catch (err) {
      console.error("[Jarvis] Scene failed to initialize (is vendor/three.min.js present?):", err);
    }

    try {
      JarvisUI.initUI();
    } catch (err) {
      console.error("[Jarvis] UI failed to initialize:", err);
    }

    setupRecognition();

    askBtn.addEventListener("click", askQuestion);
    questionInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") askQuestion();
    });
    micBtn.addEventListener("click", toggleListening);

    document.addEventListener("click", unlockAudioOnce, { once: true });
    document.addEventListener("keydown", unlockAudioOnce, { once: true });

    handleAuthenticatedArrival();
  }

  /* -------------------------------------------------------------------- */
  /* Face-login handoff                                                    */
  /* -------------------------------------------------------------------- */
  /*
   * If we arrived here via a successful face verification (login.html
   * redirects to index.html?authenticated=true), skip the wake-word wait:
   * greet immediately through the real voice pipeline, then auto-start
   * listening once the greeting actually finishes.
   *
   * Browsers can silently block autoplay audio on a fresh page load (no
   * user gesture yet on THIS page, even if one happened on login.html) --
   * JarvisState naturally goes SPEAKING -> IDLE when the greeting genuinely
   * finishes, so that's what triggers the mic. If it's blocked and that
   * transition never happens, the timeout fallback starts the mic anyway
   * rather than leaving JARVIS stuck silently waiting.
   */
  function handleAuthenticatedArrival() {
    var params = new URLSearchParams(window.location.search);
    if (params.get("authenticated") !== "true") return;

    // Remove the query param so refreshing the page doesn't replay this.
    window.history.replaceState({}, document.title, window.location.pathname);

    unlockAudioOnce();

    var started = false;
    function startListeningOnce() {
      if (started) return;
      started = true;
      if (!isListening) toggleListening();
    }

    JarvisState.onChange(function (state, previous) {
      if (previous === "SPEAKING" && state === "IDLE") startListeningOnce();
    });

    JarvisVoice.speak("Hello sir, systems online.");
    setTimeout(startListeningOnce, 4000); // fallback if the greeting audio never actually played
  }

  document.addEventListener("DOMContentLoaded", init);
})();