/* NoiseGator Modern — frontend controller */
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const state = {
    prefs: {
      input: "",
      output: "",
      threshold: -32,
      hysteresis: 5,
      attack: 30,
      release: 1000,
      volume: 0,
      auto_activate: false,
      mute: false,
      voice_filter: false,
      minimize_on_launch: false,
      minimize_to_tray: true,
      drift_compensation: false,
      check_for_updates: true,
      echo_back: 50,
      echo_back_device: "",
    },
    devices: { inputs: [], outputs: [], live: false },
    active: false,
    open: false,
    muted: false,
    level: 0,
    meters: { input: 0, output: 0, gate: 0 },
    waveform: [],
    waveHist: new Array(160).fill(0),
  };

  const SEGMENTS = 20;

  function api(path, body) {
    const opts = body === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    return fetch(path, opts).then((r) => r.json());
  }

  function patchPrefs(partial) {
    Object.assign(state.prefs, partial);
    return api("/api/prefs", partial);
  }

  /* ---------- custom selects ---------- */
  function renderSelect(el, items, selected) {
    const names = items.map((d) => d.name);
    const current = selected && names.includes(selected) ? selected : names[0] || "No devices";
    el.innerHTML = "";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "select-btn";
    btn.textContent = current;
    const menu = document.createElement("div");
    menu.className = "select-menu";
    names.forEach((name) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "select-item" + (name === current ? " active" : "");
      item.textContent = name;
      item.addEventListener("click", () => {
        el.classList.remove("open");
        const key = el.dataset.key;
        patchPrefs({ [key]: name });
        renderSelect(el, items, name);
      });
      menu.appendChild(item);
    });
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      $$(".select").forEach((s) => { if (s !== el) s.classList.remove("open"); });
      el.classList.toggle("open");
    });
    el.append(btn, menu);
    if (current && state.prefs[el.dataset.key] !== current) {
      state.prefs[el.dataset.key] = current;
    }
  }

  function renderEchoSelect(el, items, selected) {
    const names = ["Off", ...items.map((d) => d.name)];
    const raw = selected || "";
    const current = (!raw || raw === "Off") ? "Off" : (names.includes(raw) ? raw : "Off");
    el.innerHTML = "";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "select-btn";
    btn.textContent = current;
    const menu = document.createElement("div");
    menu.className = "select-menu";
    names.forEach((name) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "select-item" + (name === current ? " active" : "");
      item.textContent = name;
      item.addEventListener("click", () => {
        el.classList.remove("open");
        const stored = name === "Off" ? "" : name;
        patchPrefs({ echo_back_device: stored });
        renderEchoSelect(el, items, stored);
      });
      menu.appendChild(item);
    });
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      $$(".select").forEach((s) => { if (s !== el) s.classList.remove("open"); });
      el.classList.toggle("open");
    });
    el.append(btn, menu);
    const stored = current === "Off" ? "" : current;
    if (state.prefs.echo_back_device !== stored) {
      state.prefs.echo_back_device = stored;
    }
  }

  document.addEventListener("click", () => $$(".select").forEach((s) => s.classList.remove("open")));

  /* ---------- pills ---------- */
  function setPill(btn, on) {
    btn.classList.toggle("on", !!on);
    btn.setAttribute("aria-checked", on ? "true" : "false");
    btn.querySelector("span").textContent = on ? "ON" : "OFF";
  }

  function bindPill(id, key) {
    const btn = $(id);
    btn.addEventListener("click", () => {
      const next = !btn.classList.contains("on");
      setPill(btn, next);
      if (key === "voice_filter") {
        setPill($("#voiceToggle"), next);
        setPill($("#voiceToggle2"), next);
      }
      if (key === "mute") {
        api("/api/mute", { mute: next });
        state.prefs.mute = next;
        return;
      }
      patchPrefs({ [key]: next });
    });
  }

  bindPill("#muteToggle", "mute");
  bindPill("#autoToggle", "auto_activate");
  bindPill("#voiceToggle", "voice_filter");
  bindPill("#voiceToggle2", "voice_filter");
  bindPill("#minLaunchToggle", "minimize_on_launch");
  bindPill("#minTrayToggle", "minimize_to_tray");
  bindPill("#driftToggle", "drift_compensation");
  bindPill("#updateToggle", "check_for_updates");

  /* ---------- sliders ---------- */
  function paintRange(el) {
    const min = Number(el.min);
    const max = Number(el.max);
    const val = Number(el.value);
    const pct = ((val - min) / (max - min)) * 100;
    el.style.background = `linear-gradient(90deg,
      #14b8a6 0%, #2ee6c7 ${pct * 0.45}%, #7c3aed ${Math.max(pct, 8)}%,
      #1c2436 ${pct}%, #1c2436 100%)`;
  }

  function bindSlider(id, key, fmt) {
    const el = $(id);
    const label = $(id + "Val");
    const apply = () => {
      label.textContent = fmt(Number(el.value));
      paintRange(el);
    };
    el.addEventListener("input", () => {
      apply();
      const n = Number(el.value);
      state.prefs[key] = n;
      patchPrefs({ [key]: n });
    });
    apply();
  }

  bindSlider("#attack", "attack", (v) => `${Math.round(v)} ms`);
  bindSlider("#release", "release", (v) => `${Math.round(v)} ms`);
  bindSlider("#hysteresis", "hysteresis", (v) => `${Math.round(v)} dB`);
  bindSlider("#volume", "volume", (v) => `${v} dB`);
  bindSlider("#echo_back", "echo_back", (v) => `${Math.round(v)}%`);

  /* ---------- meters ---------- */
  function buildMeter(col) {
    col.innerHTML = "";
    for (let i = 0; i < SEGMENTS; i++) {
      const s = document.createElement("div");
      s.className = "seg";
      col.appendChild(s);
    }
  }
  buildMeter($("#meterA"));
  buildMeter($("#meterB"));
  buildMeter($("#meterC"));

  function paintMeter(col, value) {
    const lit = Math.round(Math.max(0, Math.min(1, value)) * SEGMENTS);
    [...col.children].forEach((seg, i) => {
      const on = i < lit;
      seg.className = "seg";
      if (!on) return;
      const ratio = i / SEGMENTS;
      let band = "low";
      if (ratio > 0.82) band = "peak";
      else if (ratio > 0.62) band = "high";
      else if (ratio > 0.38) band = "mid";
      seg.classList.add("on", band);
    });
  }

  function formatDbNumber(db) {
    const n = Math.round(Number(db));
    if (!Number.isFinite(n)) return "\u2212\u221e";
    if (n === 0) return "0";
    return (n < 0 ? "\u2212" : "+") + String(Math.abs(n));
  }

  function formatDb(db, digits) {
    const n = Number(db);
    if (!Number.isFinite(n)) return "\u2212\u221e dB";
    const d = digits === undefined ? 1 : digits;
    if (n === 0) return (0).toFixed(d) + " dB";
    return (n < 0 ? "\u2212" : "+") + Math.abs(n).toFixed(d) + " dB";
  }

  /* ---------- knob ---------- */
  const knob = $("#knob");
  const kctx = knob.getContext("2d");
  const KMIN = -60;
  const KMAX = 0;

  function threshToAngle(t) {
    const span = Math.PI * 1.5;
    const start = Math.PI * 0.75;
    const n = (t - KMIN) / (KMAX - KMIN);
    return start + n * span;
  }

  function drawKnob(value) {
    const w = knob.width;
    const h = knob.height;
    const cx = w / 2;
    const cy = h / 2;
    const r = w * 0.38;
    kctx.clearRect(0, 0, w, h);

    const start = Math.PI * 0.75;
    const span = Math.PI * 1.5;
    const ang = threshToAngle(value);

    kctx.save();
    kctx.beginPath();
    kctx.arc(cx, cy, r + 16, 0, Math.PI * 2);
    const glow = kctx.createRadialGradient(cx, cy, r * 0.2, cx, cy, r + 22);
    glow.addColorStop(0, "rgba(46,230,199,0.10)");
    glow.addColorStop(1, "rgba(124,58,237,0.0)");
    kctx.fillStyle = glow;
    kctx.fill();

    kctx.lineWidth = 10;
    kctx.strokeStyle = "rgba(255,255,255,0.06)";
    kctx.lineCap = "round";
    kctx.beginPath();
    kctx.arc(cx, cy, r, start, start + span);
    kctx.stroke();

    const grad = kctx.createLinearGradient(0, 0, w, h);
    grad.addColorStop(0, "#2ee6c7");
    grad.addColorStop(1, "#b56bff");
    kctx.strokeStyle = grad;
    kctx.shadowColor = "rgba(46,230,199,0.55)";
    kctx.shadowBlur = 14;
    kctx.beginPath();
    kctx.arc(cx, cy, r, start, ang);
    kctx.stroke();
    kctx.shadowBlur = 0;

    for (let i = 0; i <= 16; i++) {
      const a = start + (span * i) / 16;
      const inner = r + 10;
      const outer = r + (i % 4 === 0 ? 18 : 14);
      kctx.strokeStyle = i % 4 === 0 ? "rgba(243,246,251,0.35)" : "rgba(243,246,251,0.12)";
      kctx.lineWidth = 1.5;
      kctx.beginPath();
      kctx.moveTo(cx + Math.cos(a) * inner, cy + Math.sin(a) * inner);
      kctx.lineTo(cx + Math.cos(a) * outer, cy + Math.sin(a) * outer);
      kctx.stroke();
    }

    kctx.beginPath();
    kctx.arc(cx, cy, r - 16, 0, Math.PI * 2);
    const disc = kctx.createLinearGradient(cx - r, cy - r, cx + r, cy + r);
    disc.addColorStop(0, "rgba(255,255,255,0.10)");
    disc.addColorStop(1, "rgba(10,16,28,0.85)");
    kctx.fillStyle = disc;
    kctx.fill();
    kctx.strokeStyle = "rgba(255,255,255,0.12)";
    kctx.lineWidth = 1.4;
    kctx.stroke();

    const px = cx + Math.cos(ang) * (r - 28);
    const py = cy + Math.sin(ang) * (r - 28);
    kctx.beginPath();
    kctx.arc(px, py, 6, 0, Math.PI * 2);
    kctx.fillStyle = "#f4fffc";
    kctx.shadowColor = "#2ee6c7";
    kctx.shadowBlur = 12;
    kctx.fill();
    kctx.restore();

    $("#knobValue").textContent = formatDbNumber(value);
  }

  function angleToThresh(clientX, clientY) {
    const rect = knob.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    let a = Math.atan2(clientY - cy, clientX - cx);
    const start = Math.PI * 0.75;
    let rel = a - start;
    while (rel < 0) rel += Math.PI * 2;
    const span = Math.PI * 1.5;
    const n = Math.max(0, Math.min(1, rel / span));
    return Math.round(KMIN + n * (KMAX - KMIN));
  }

  let dragging = false;
  function onKnob(ev) {
    const t = angleToThresh(ev.clientX, ev.clientY);
    state.prefs.threshold = t;
    drawKnob(t);
    patchPrefs({ threshold: t });
  }
  knob.addEventListener("pointerdown", (ev) => {
    dragging = true;
    knob.setPointerCapture(ev.pointerId);
    onKnob(ev);
  });
  knob.addEventListener("pointermove", (ev) => { if (dragging) onKnob(ev); });
  knob.addEventListener("pointerup", () => { dragging = false; });
  knob.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const next = Math.max(KMIN, Math.min(KMAX, state.prefs.threshold + (ev.deltaY < 0 ? 1 : -1)));
    state.prefs.threshold = next;
    drawKnob(next);
    patchPrefs({ threshold: next });
  }, { passive: false });

  /* ---------- waveform ---------- */
  const wave = $("#wave");
  const wctx = wave.getContext("2d");

  function drawWave(peaks) {
    const w = wave.width;
    const h = wave.height;
    wctx.clearRect(0, 0, w, h);

    const g = wctx.createLinearGradient(0, 0, w, 0);
    g.addColorStop(0, "rgba(181,107,255,0.08)");
    g.addColorStop(1, "rgba(46,230,199,0.08)");
    wctx.fillStyle = g;
    wctx.fillRect(0, 0, w, h);

    wctx.strokeStyle = "rgba(255,255,255,0.04)";
    wctx.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
      const y = (h / 4) * i;
      wctx.beginPath();
      wctx.moveTo(0, y);
      wctx.lineTo(w, y);
      wctx.stroke();
    }

    if (!peaks || !peaks.length) return;
    const n = peaks.length;
    const mid = h / 2;
    const step = w / (n - 1);

    wctx.beginPath();
    wctx.moveTo(0, mid);
    for (let i = 0; i < n; i++) {
      const y = mid - Math.min(1, peaks[i] * 2.4) * (h * 0.44);
      wctx.lineTo(i * step, y);
    }
    for (let i = n - 1; i >= 0; i--) {
      const y = mid + Math.min(1, peaks[i] * 2.4) * (h * 0.44);
      wctx.lineTo(i * step, y);
    }
    wctx.closePath();
    const fill = wctx.createLinearGradient(0, 0, w, 0);
    fill.addColorStop(0, "rgba(181,107,255,0.55)");
    fill.addColorStop(0.55, "rgba(46,230,199,0.38)");
    fill.addColorStop(1, "rgba(46,230,199,0.62)");
    wctx.fillStyle = fill;
    wctx.fill();

    wctx.beginPath();
    for (let i = 0; i < n; i++) {
      const y = mid - Math.min(1, peaks[i] * 2.4) * (h * 0.44);
      if (i === 0) wctx.moveTo(0, y);
      else wctx.lineTo(i * step, y);
    }
    wctx.strokeStyle = "rgba(244,255,252,0.85)";
    wctx.lineWidth = 2;
    wctx.shadowColor = "rgba(46,230,199,0.6)";
    wctx.shadowBlur = 10;
    wctx.stroke();
    wctx.shadowBlur = 0;

    const db = Number(state.prefs.threshold);
    if (Number.isFinite(db)) {
      const amp = Math.pow(10, db / 20);
      const y = mid - Math.min(1, amp * 2.4) * (h * 0.44);
      wctx.save();
      wctx.setLineDash([7, 5]);
      wctx.strokeStyle = "rgba(46,230,199,0.88)";
      wctx.shadowColor = "rgba(46,230,199,0.75)";
      wctx.shadowBlur = 10;
      wctx.lineWidth = 1.6;
      wctx.beginPath();
      wctx.moveTo(0, y);
      wctx.lineTo(w, y);
      wctx.stroke();
      wctx.restore();
      wctx.font = "600 13px 'JetBrains Mono', ui-monospace, monospace";
      wctx.fillStyle = "rgba(46,230,199,0.95)";
      wctx.textAlign = "right";
      wctx.textBaseline = "bottom";
      const labelY = y < 18 ? y + 16 : y - 5;
      wctx.fillText(formatDb(db, 0), w - 10, labelY);
    }
  }

  function pushWave(block) {
    if (!block || !block.length) return;
    const hist = state.waveHist;
    const take = Math.max(1, Math.floor(block.length / 8));
    for (let i = 0; i < take; i++) {
      hist.shift();
      hist.push(block[Math.floor((i / take) * block.length)] || 0);
    }
  }

  /* ---------- chrome / status ---------- */
  function setStatus(open, muted, active) {
    const pill = $("#statusPill");
    const text = $("#statusText");
    pill.classList.remove("open", "closed", "muted");
    if (muted) {
      pill.classList.add("muted");
      text.textContent = "MUTED";
    } else if (open) {
      pill.classList.add("open");
      text.textContent = "OPEN";
    } else {
      pill.classList.add("closed");
      text.textContent = "CLOSED";
    }
    const chip = $("#modeChip");
    if (!active) chip.textContent = "STANDBY";
    else if (state.devices.live === false) chip.textContent = "DEMO";
    else chip.textContent = "LIVE";
  }

  function applyPrefsToUI(p) {
    $("#attack").value = p.attack;
    $("#release").value = p.release;
    $("#hysteresis").value = p.hysteresis ?? 5;
    $("#volume").value = p.volume;
    $("#echo_back").value = p.echo_back ?? 50;
    $("#attackVal").textContent = `${p.attack} ms`;
    $("#releaseVal").textContent = `${p.release} ms`;
    $("#hysteresisVal").textContent = `${p.hysteresis ?? 5} dB`;
    $("#volumeVal").textContent = `${p.volume} dB`;
    $("#echo_backVal").textContent = `${p.echo_back ?? 50}%`;
    paintRange($("#attack"));
    paintRange($("#release"));
    paintRange($("#hysteresis"));
    paintRange($("#volume"));
    paintRange($("#echo_back"));
    drawKnob(p.threshold);
    setPill($("#muteToggle"), p.mute);
    setPill($("#autoToggle"), p.auto_activate);
    setPill($("#voiceToggle"), p.voice_filter);
    setPill($("#voiceToggle2"), p.voice_filter);
    setPill($("#minLaunchToggle"), p.minimize_on_launch);
    setPill($("#minTrayToggle"), p.minimize_to_tray);
    setPill($("#driftToggle"), p.drift_compensation);
    setPill($("#updateToggle"), p.check_for_updates);
  }

  function setActiveUI(on) {
    const btn = $("#activateBtn");
    btn.disabled = !!on;
    btn.classList.toggle("active", !!on);
    btn.querySelector(".activate-label").textContent = on ? "Active" : "Activate";
    $("#footHint").textContent = on ? "Gate running" : "Waiting for activate";
  }

  $("#activateBtn").addEventListener("click", async () => {
    if (state.active) return;
    const res = await api("/api/activate", {
      input: state.prefs.input,
      output: state.prefs.output,
      echo_back_device: state.prefs.echo_back_device,
    });
    if (res && res.state) ingest(res.state);
    setActiveUI(true);
  });

  /* ---------- settings drawer ---------- */
  const drawer = $("#drawer");
  const overlay = $("#overlay");
  function openSettings(open) {
    drawer.classList.toggle("open", open);
    drawer.setAttribute("aria-hidden", open ? "false" : "true");
    overlay.hidden = !open;
  }
  $("#settingsBtn").addEventListener("click", () => openSettings(true));
  $("#closeSettings").addEventListener("click", () => openSettings(false));
  overlay.addEventListener("click", () => openSettings(false));

  $("#resetBtn").addEventListener("click", async () => {
    const res = await api("/api/reset", {});
    if (res && res.prefs) {
      state.prefs = { ...state.prefs, ...res.prefs };
      applyPrefsToUI(state.prefs);
    }
  });

  /* ---------- unlimited trial nag (fail-open; never gates the app) ---------- */
  const SUPPORT_URL = "https://github.com/berkkarabacak/noisegator";

  function hideTrialNag() {
    const el = $("#trialOverlay");
    if (el) el.hidden = true;
  }

  function showTrialNag(day) {
    const overlay = $("#trialOverlay");
    if (!overlay || overlay.dataset.shown === "1") return;
    overlay.dataset.shown = "1";
    const n = Number(day);
    const safe = Number.isFinite(n) && n >= 1 ? Math.floor(n) : 1;
    const line = $("#trialDay");
    if (line) line.textContent = `Day ${safe} of your complimentary evaluation.`;
    overlay.hidden = false;
    const cont = $("#trialContinue");
    if (cont) cont.focus();
  }

  const trialContinue = $("#trialContinue");
  const trialSupport = $("#trialSupport");
  const trialOverlay = $("#trialOverlay");
  if (trialContinue) trialContinue.addEventListener("click", hideTrialNag);
  if (trialOverlay) {
    trialOverlay.addEventListener("click", (e) => {
      if (e.target === trialOverlay) hideTrialNag();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && trialOverlay && !trialOverlay.hidden) hideTrialNag();
  });
  if (trialSupport) {
    trialSupport.addEventListener("click", async () => {
      let opened = false;
      try {
        const res = await api("/api/open-url", { url: SUPPORT_URL });
        opened = !!(res && res.ok);
      } catch (_) { /* fail-open: try the browser next */ }
      if (!opened) {
        try { window.open(SUPPORT_URL, "_blank", "noopener"); } catch (_) { /* still usable */ }
      }
    });
  }

  /* ---------- ingest / poll ---------- */
  function ingest(s) {
    if (s.prefs) {
      state.prefs = { ...state.prefs, ...s.prefs };
    }
    if (s.devices) {
      const sig = JSON.stringify(s.devices) + "|" + (s.prefs && s.prefs.input) + "|" + (s.prefs && s.prefs.output) + "|" + (s.prefs && s.prefs.echo_back_device);
      if (state._devSig !== sig) {
        state._devSig = sig;
        state.devices = s.devices;
        renderSelect($("#inputSelect"), s.devices.inputs || [], state.prefs.input);
        renderSelect($("#outputSelect"), s.devices.outputs || [], state.prefs.output);
        renderEchoSelect($("#echoSelect"), s.devices.outputs || [], state.prefs.echo_back_device);
      }
    }
    state.active = !!s.active;
    state.open = !!s.open;
    state.muted = !!s.muted;
    const db = (s.level_db !== undefined && s.level_db !== null) ? s.level_db : (s.level || 0);
    state.level = db;
    if (s.threshold !== undefined) state.prefs.threshold = s.threshold;
    if (s.hysteresis !== undefined) state.prefs.hysteresis = s.hysteresis;
    state.meters.input = s.input_meter || 0;
    state.meters.output = s.output_meter || 0;
    state.meters.gate = s.gate_meter || 0;
    if (s.waveform) {
      state.waveform = s.waveform;
      if (s.active) pushWave(s.waveform);
    }
    $("#levelValue").textContent = formatDb(state.level, 1);
    setStatus(state.open, state.muted, state.active);
    setActiveUI(state.active);
    if (s.demo) $("#waveHint").textContent = "demo \u00b7 44.1 kHz";
    else if (s.live_audio) $("#waveHint").textContent = "input \u00b7 44.1 kHz";
    if (s.active) $("#footHint").textContent = s.demo ? "Demo meters (no live capture)" : "Gate running";
  }

  function paint() {
    paintMeter($("#meterA"), state.meters.input);
    paintMeter($("#meterB"), state.meters.output);
    paintMeter($("#meterC"), state.meters.gate);
    drawWave(state.waveform.length ? state.waveform : state.waveHist);
    requestAnimationFrame(paint);
  }

  async function boot() {
    let day = 1;
    try {
      const s = await api("/api/state");
      ingest(s);
      applyPrefsToUI(state.prefs);
      if (s && s.trial && s.trial.day != null) day = s.trial.day;
    } catch (err) {
      applyPrefsToUI(state.prefs);
      drawKnob(state.prefs.threshold);
    }
    paint();
    requestAnimationFrame(() => showTrialNag(day));
    setInterval(async () => {
      try {
        const s = await api("/api/state");
        ingest(s);
      } catch (_) { /* keep last frame */ }
    }, 50);
  }

  boot();
})();
