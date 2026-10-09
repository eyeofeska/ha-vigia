/* Vigia: wildfire watch cards for Home Assistant. Served and registered by the Vigia integration.
   custom:vigia-card      fire risk tile with alert settings; tap opens the fire map
   custom:vigia-map-card  the fire map on its own, for a pop-up or a dashboard view
   https://github.com/eyeofeska/ha-vigia (MIT) */
(() => {
const VERSION = "0.1.3";
const LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/";
const LEVELS = [null,
  { name: "low", color: "#3DAA5C" }, { name: "moderate", color: "#D9A400" }, { name: "high", color: "#F07F1A" },
  { name: "very high", color: "#E0402C" }, { name: "maximum", color: "#9B1C3F" }];
const ADVICE = {
  3: "No outdoor fires or burning. Careful with sparks: angle grinders, brush cutters, chainsaws.",
  4: "No outdoor fires or burning. Avoid angle grinders, brush cutters and chainsaws outside. Keep a hose ready.",
  5: "No outdoor fires or burning. No spark-making tools outside. Keep hoses ready and water stored.",
};
const WARN = { yellow: "#E5B800", orange: "#F07F1A", red: "#E0402C" };
const TIER = {
  close: { color: "#C62828", word: "fire close" }, upwind: { color: "#E0402C", word: "fire upwind" },
  watch: { color: "#F07F1A", word: "fire watch" },
};
const SETTING_ORDER = [
  ["close_km", "close", "any wind"], ["upwind_km", "upwind", "wind from the fire"], ["watch_km", "watch", "upwind, strong wind"],
  ["wind_angle", "wind angle", "counts as from the fire"], ["min_wind", "upwind wind", "at least"], ["watch_wind", "watch wind", "at least"],
];
const SOURCE_NAMES = { ipma_risk: "IPMA risk", ipma_warnings: "IPMA warnings", fogos: "fogos.pt", firms: "NASA FIRMS", effis: "EFFIS", wind: "Open-Meteo wind" };
// satellite detections by age: [max hours, fill, layer opacity]
const AGES = [[6, "#FF3B1F", 0.92], [12, "#FF7A1A", 0.72], [24, "#E8963F", 0.48], [48, "#9A7B63", 0.26]];
const FIRE_PATH = "M17.66 11.2C17.43 10.9 17.15 10.64 16.89 10.38C16.22 9.78 15.46 9.35 14.82 8.72C13.33 7.26 13 4.85 13.95 3C13 3.23 12.17 3.75 11.46 4.32C8.87 6.4 7.85 10.07 9.07 13.22C9.11 13.32 9.15 13.42 9.15 13.55C9.15 13.77 9 13.97 8.8 14.05C8.57 14.15 8.33 14.09 8.14 13.93C8.08 13.88 8.04 13.83 8 13.76C6.87 12.33 6.69 10.28 7.45 8.64C5.78 10 4.87 12.3 5 14.47C5.06 14.97 5.12 15.47 5.29 15.97C5.43 16.57 5.7 17.17 6 17.7C7.08 19.43 8.95 20.67 10.96 20.92C13.1 21.19 15.39 20.8 17.03 19.32C18.86 17.66 19.5 15 18.56 12.72L18.43 12.46C18.22 12 17.66 11.2 17.66 11.2Z";

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const km = d => d == null ? "—" : Number.isInteger(d) ? String(d) : d < 10 ? d.toFixed(1) : String(Math.round(d));
const hhmm = iso => iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
const ago = h => h == null ? "" : h < 1 ? Math.max(1, Math.round(h * 60)) + " min ago" : h < 48 ? Math.round(h) + " h ago" : Math.round(h / 24) + " days ago";
const hoursSince = iso => iso ? (Date.now() - Date.parse(iso)) / 3.6e6 : null;
const navigate = path => {
  history.pushState(null, "", path.startsWith("#") ? location.pathname + location.search + path : path);
  window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
};

// ---------- one shared subscription to the integration ----------
const hub = {
  conn: null, unsub: null, data: null, error: null, listeners: new Set(),
  attach(hass, fn) {
    this.listeners.add(fn);
    if (this.conn !== hass.connection) {
      if (this.unsub) this.unsub.then(u => u && u()).catch(() => {});
      this.conn = hass.connection;
      this.error = null;
      this.unsub = hass.connection.subscribeMessage(d => { this.data = d; this.error = null; this.emit(); }, { type: "vigia/subscribe" })
        .catch(e => { this.error = (e && e.message) || "vigia is not set up"; this.emit(); return null; });
    }
    if (this.data || this.error) fn();
  },
  detach(fn) { this.listeners.delete(fn); },
  emit() { this.listeners.forEach(f => { try { f(); } catch (e) { console.error(e); } }); },
};

// ---------- Leaflet, loaded once from cdnjs ----------
let leafletP = null;
const loadLeaflet = () => leafletP || (leafletP = new Promise((ok, fail) => {
  if (window.L && window.L.map) return ok(window.L);
  const s = document.createElement("script");
  s.src = LEAFLET + "leaflet.min.js";
  s.onload = () => ok(window.L);
  s.onerror = () => { leafletP = null; fail(new Error("map library could not load (offline?)")); };
  document.head.appendChild(s);
}));

// ======================================================================
// The tile
// ======================================================================
class VigiaCard extends HTMLElement {
  setConfig(c) {
    this._c = { map: true, ...c };
    this._open = false;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._build();
    this._onData = () => this._render();
  }
  static getStubConfig() { return {}; }
  getCardSize() { return 2; }
  getGridOptions() { return { columns: "full", rows: "auto" }; }
  set hass(h) {
    this._hass = h;
    if (!this._attached && this.isConnected) { this._attached = true; hub.attach(h, this._onData); }
  }
  connectedCallback() { if (this._hass && !this._attached) { this._attached = true; hub.attach(this._hass, this._onData); } }
  disconnectedCallback() { hub.detach(this._onData); this._attached = false; }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; }
        ha-card { display:block; box-sizing:border-box; padding:10px 14px; font-family:'Lato',sans-serif; transition:background .3s; }
        .row { display:flex; align-items:center; gap:12px; cursor:pointer; -webkit-tap-highlight-color:transparent; }
        .ico { width:36px; height:36px; flex:none; border-radius:50%; display:flex; align-items:center; justify-content:center; }
        .ico ha-icon { --mdc-icon-size:21px; }
        .main { flex:1 1 auto; min-width:0; }
        .title { font-size:15px; color:var(--primary-text-color); line-height:1.25; }
        .title b { font-weight:800; }
        .sub { font-size:12.5px; color:var(--secondary-text-color); line-height:1.35; margin-top:1px; }
        .meter { display:flex; gap:3px; flex:none; }
        .meter i { display:block; width:14px; height:8px; border-radius:3px; background:color-mix(in srgb, var(--primary-text-color) 10%, transparent); }
        .gear { all:unset; flex:none; width:32px; height:32px; border-radius:50%; display:flex; align-items:center; justify-content:center; cursor:pointer;
          color:var(--secondary-text-color); -webkit-tap-highlight-color:transparent; }
        .gear:hover { background:color-mix(in srgb, var(--primary-text-color) 7%, transparent); }
        .gear ha-icon { --mdc-icon-size:19px; }
        .gear.on { color:var(--primary-text-color); background:color-mix(in srgb, var(--primary-text-color) 9%, transparent); }
        .lines { margin:8px 0 0 48px; display:flex; flex-direction:column; gap:4px; }
        .lines:empty { display:none; }
        .line { display:flex; gap:6px; align-items:flex-start; font-size:13.5px; line-height:1.4; }
        .line ha-icon { --mdc-icon-size:16px; flex:none; margin-top:2px; }
        .line a { color:inherit; }
        .pill { display:inline-block; padding:0 7px; border-radius:999px; font-size:11.5px; font-weight:700; color:#fff; margin-left:4px; vertical-align:1px; }
        .panel { margin:10px 0 2px; padding-top:10px; border-top:1px solid color-mix(in srgb, var(--primary-text-color) 10%, transparent); display:none; }
        .panel.open { display:block; }
        .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:8px; }
        .set { border-radius:12px; padding:7px 9px; background:color-mix(in srgb, var(--primary-text-color) 4%, transparent); }
        .set .lbl { font-size:12px; font-weight:700; color:var(--primary-text-color); }
        .set .hint { font-size:11px; color:var(--secondary-text-color); }
        .step { display:flex; align-items:center; justify-content:space-between; margin-top:4px; }
        .step button { all:unset; width:28px; height:28px; border-radius:50%; text-align:center; line-height:28px; font-size:18px; cursor:pointer;
          background:color-mix(in srgb, var(--primary-text-color) 8%, transparent); color:var(--primary-text-color); -webkit-tap-highlight-color:transparent; }
        .step button:active { transform:scale(.92); }
        .step .v { font-size:15px; font-weight:700; color:var(--primary-text-color); }
        .actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
        .btn { all:unset; cursor:pointer; font-size:13px; font-weight:700; padding:7px 12px; border-radius:999px; -webkit-tap-highlight-color:transparent;
          background:color-mix(in srgb, var(--primary-text-color) 8%, transparent); color:var(--primary-text-color); }
        .btn.hot { background:#E0402C; color:#fff; }
        .btn:active { transform:scale(.96); }
        .sources { display:flex; flex-wrap:wrap; gap:4px 12px; margin-top:10px; font-size:11.5px; color:var(--secondary-text-color); }
        .sources span { display:inline-flex; align-items:center; gap:5px; }
        .dot { width:7px; height:7px; border-radius:50%; display:inline-block; }
        .note { font-size:11.5px; color:var(--secondary-text-color); margin-top:8px; line-height:1.4; }
        @media (max-width: 480px) { .meter i { width:9px; } .lines { margin-left:0; } }
      </style>
      <ha-card>
        <div class="row" tabindex="0" role="button">
          <div class="ico"><ha-icon icon="mdi:fire"></ha-icon></div>
          <div class="main"><div class="title">fire risk…</div><div class="sub"></div></div>
          <div class="meter"></div>
          <button class="gear" title="alert settings"><ha-icon icon="mdi:tune-variant"></ha-icon></button>
        </div>
        <div class="lines"></div>
        <div class="panel"></div>
      </ha-card>`;
    const $ = s => this.shadowRoot.querySelector(s);
    const open = () => {
      if (this._c.navigate) return navigate(this._c.navigate);
      if (this._c.map !== false) VigiaMapDialog.open(this._hass);
    };
    $(".row").addEventListener("click", e => { if (!e.target.closest(".gear")) open(); });
    $(".row").addEventListener("keydown", e => { if (e.key === "Enter") open(); });
    $(".gear").addEventListener("click", e => { e.stopPropagation(); this._open = !this._open; this._render(); });
    $(".panel").addEventListener("click", e => this._panelClick(e));
  }

  _render() {
    const $ = s => this.shadowRoot.querySelector(s);
    if (!$(".title")) return;
    if (hub.error && !hub.data) {
      $(".title").innerHTML = `fire risk <b>unavailable</b>`;
      $(".sub").textContent = hub.error;
      return;
    }
    const d = hub.data;
    if (!d) return;
    const today = (d.risk && d.risk.today) || {}, tom = (d.risk && d.risk.tomorrow) || {};
    const lvl = today.level || 0, L = LEVELS[lvl];
    const tier = d.alert && d.alert.tier !== "none" ? d.alert.tier : null, threat = d.alert && d.alert.threat;
    const accent = tier ? TIER[tier].color : L ? L.color : "#9AA0A6";
    const loud = lvl >= 3 || tier;
    const card = $("ha-card");
    card.style.background = loud ? `color-mix(in srgb, ${accent} ${tier ? 16 : 11}%, var(--ha-card-background, var(--card-background-color, #fff)))` : "";
    card.style.border = loud ? `1.5px solid color-mix(in srgb, ${accent} 45%, transparent)` : "";
    const ico = $(".ico");
    ico.style.background = `color-mix(in srgb, ${accent} 16%, transparent)`;
    ico.querySelector("ha-icon").style.color = accent;
    ico.querySelector("ha-icon").setAttribute("icon", tier ? "mdi:fire-alert" : "mdi:fire");

    const wl = d.warning_level, wcol = WARN[wl];
    const wtype = wcol && (d.warnings || []).find(w => w.level === wl);
    $(".title").innerHTML = L
      ? `fire risk <b style="color:${L.color}">${L.name}</b>${wcol ? `<span class="pill" style="background:${wcol}">${esc(wl)} ${esc(((wtype && wtype.type) || "weather").toLowerCase())} warning</span>` : ""}`
      : `fire risk <b>unavailable</b>`;

    const bits = [];
    const T = LEVELS[tom.level];
    if (T) bits.push(`tomorrow ${T.name}`);
    const n = (d.counts.incidents || 0) + (d.counts.hotspots || 0);
    bits.push(n ? `${n} fire${n > 1 ? "s" : ""} within ${Math.round(d.radius_km)} km, nearest ${km(d.nearest && d.nearest.distance)} km ${(d.nearest && d.nearest.dir) || ""}`
      : `no fires within ${Math.round(d.radius_km)} km`);
    const live = Object.values(d.sources).filter(s => s.enabled);
    const stale = live.length && live.every(s => !s.ok);
    if (stale) {
      const last = live.map(s => s.updated).filter(Boolean).sort().pop();
      bits.push(`offline, last update ${hhmm(last)}`);
    }
    $(".sub").textContent = bits.join(" · ");
    $(".meter").innerHTML = [1, 2, 3, 4, 5].map(i => `<i style="${L && i <= lvl ? `background:${LEVELS[i].color}` : ""}"></i>`).join("");

    const lines = [];
    if (tier && threat) lines.push({ icon: "mdi:fire-alert", color: TIER[tier].color, html: `${esc(threat.message)} <a href="${esc(threat.url)}" target="_blank" rel="noopener">fogos.pt</a>` });
    if (ADVICE[lvl]) lines.push({ icon: "mdi:alert-outline", color: "var(--primary-text-color)", iconColor: accent, html: esc(ADVICE[lvl]) });
    if (d.test_mode) lines.push({ icon: "mdi:test-tube", color: "var(--secondary-text-color)", html: "test mode: the fire shown is pretend" });
    $(".lines").innerHTML = lines.map(l => `<div class="line" style="color:${l.color}"><ha-icon icon="${l.icon}" style="color:${l.iconColor || l.color}"></ha-icon><span>${l.html}</span></div>`).join("");

    $(".gear").classList.toggle("on", this._open);
    const panel = $(".panel");
    panel.classList.toggle("open", this._open);
    if (this._open) panel.innerHTML = this._panel(d);
  }

  _panel(d) {
    const e = d.entities || {}, s = d.settings || {};
    const st = this._hass && this._hass.states;
    const unit = k => (st && e[k] && st[e[k]] && st[e[k]].attributes.unit_of_measurement) || "";
    const fmt = (k, v) => (Number.isInteger(v) ? v : v.toFixed(1)) + " " + unit(k);
    const sets = SETTING_ORDER.map(([k, lbl, hint]) => `
      <div class="set"><div class="lbl">${esc(lbl)}</div><div class="hint">${esc(hint)}</div>
        <div class="step"><button data-k="${k}" data-d="-1">−</button><span class="v">${fmt(k, s[k] ?? 0)}</span><button data-k="${k}" data-d="1">+</button></div></div>`).join("");
    const src = Object.entries(d.sources).filter(([, v]) => v.enabled).map(([k, v]) =>
      `<span title="${esc(v.error || "")}"><i class="dot" style="background:${v.ok ? "#3DAA5C" : v.updated ? "#D9A400" : "#E0402C"}"></i>${esc(SOURCE_NAMES[k] || k)} ${v.ok ? hhmm(v.updated) : esc(v.error || "waiting")}</span>`).join("");
    const firms = d.sources.firms && d.sources.firms.enabled ? "" :
      `<div class="note">Satellite hotspots are off. Add a free NASA FIRMS map key in Settings › Devices & services › Vigia › Configure.</div>`;
    const fg = d.sources.fogos;
    const fogosNote = fg && fg.enabled && /429|rate/i.test(fg.error || "")
      ? `<div class="note">fogos.pt is limiting requests from this connection. Request your own free key at fogos.pt/en/api and add it in Settings › Devices & services › Vigia › Configure.</div>` : "";
    return `<div class="grid">${sets}</div>
      <div class="actions">
        <button class="btn${d.test_mode ? " hot" : ""}" data-a="test">${d.test_mode ? "end test fire" : "show a test fire"}</button>
        <button class="btn" data-a="alert">send test alert</button>
        <button class="btn" data-a="map">open map</button>
      </div>
      <div class="sources">${src}</div>${firms}${fogosNote}`;
  }

  _panelClick(ev) {
    const b = ev.target.closest("button");
    if (!b || !this._hass || !hub.data) return;
    const e = hub.data.entities || {};
    if (b.dataset.k) {
      const id = e[b.dataset.k], st = id && this._hass.states[id];
      if (!st) return;
      const a = st.attributes, step = a.step || 1;
      const v = Math.min(a.max, Math.max(a.min, Math.round((parseFloat(st.state) + step * +b.dataset.d) / step) * step));
      this._hass.callService("number", "set_value", { entity_id: id, value: v });
    } else if (b.dataset.a === "test" && e.test_mode) {
      this._hass.callService("switch", hub.data.test_mode ? "turn_off" : "turn_on", { entity_id: e.test_mode });
    } else if (b.dataset.a === "alert" && e.test_alert) {
      this._hass.callService("button", "press", { entity_id: e.test_alert });
      b.textContent = "sent";
      setTimeout(() => { b.textContent = "send test alert"; }, 2500);
    } else if (b.dataset.a === "map") {
      VigiaMapDialog.open(this._hass);
    }
  }
}

// ======================================================================
// The map (used by the dialog and by vigia-map-card)
// ======================================================================
class VigiaMap extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="${LEAFLET}leaflet.min.css">
      <style>
        :host { display:block; position:relative; height:100%; min-height:240px; font-family:'Lato',sans-serif; }
        #map { position:absolute; inset:0; background:#E9E5DC; border-radius:inherit; }
        .msg { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:#555; font-size:13px; text-align:center; padding:20px; }
        .leaflet-container { font-family:'Lato',sans-serif; }
        .hill { mix-blend-mode:multiply; }
        .leaflet-popup-content { font-size:13px; line-height:1.45; margin:10px 12px; }
        .leaflet-popup-content b { font-weight:800; }
        .leaflet-popup-content a { color:#C2410C; font-weight:700; }
        .leaflet-control-attribution { font-size:10px; }
        .inc { width:26px; height:26px; border-radius:50%; display:flex; align-items:center; justify-content:center; border:2px solid #fff;
          box-shadow:0 1px 4px rgba(0,0,0,.35); }
        .inc svg { width:16px; height:16px; fill:#fff; }
        .inc.active { background:#E0402C; animation:pulse 1.6s ease-out infinite; }
        .inc.resolving { background:#F07F1A; }
        .inc.ended { background:#8A8F94; }
        .inc.test { outline:2px dashed #7c3aed; outline-offset:2px; }
        @keyframes pulse { 0% { box-shadow:0 0 0 0 rgba(224,64,44,.55); } 100% { box-shadow:0 0 0 14px rgba(224,64,44,0); } }
        .home { width:14px; height:14px; border-radius:50%; background:#1F2937; border:3px solid #fff; box-shadow:0 1px 4px rgba(0,0,0,.4); }
        .wind { display:flex; flex-direction:column; align-items:center; pointer-events:none; }
        .wind svg { overflow:visible; }
        .wind span { font-size:10px; font-weight:700; color:#33415C; text-shadow:0 0 3px #fff, 0 0 3px #fff; margin-top:-2px; }
        .ringlbl { background:rgba(255,255,255,.85); border:none; border-radius:999px; box-shadow:none; font-size:10.5px; font-weight:700; padding:0 6px; line-height:16px; }
        .ringlbl::before { display:none; }
        .legend { position:absolute; left:10px; bottom:22px; z-index:500; background:rgba(255,255,255,.92); border-radius:12px; padding:8px 10px;
          font-size:11.5px; color:#333; box-shadow:0 1px 5px rgba(0,0,0,.18); line-height:1.6; max-width:200px; }
        .legend .r { display:flex; align-items:center; gap:7px; }
        .legend i { display:inline-block; width:12px; height:12px; border-radius:50%; flex:none; }
        .legend .ages { display:flex; gap:3px; }
        .legend .ages i { width:10px; height:10px; }
        .legend.min .full { display:none; }
        .legend .tog { cursor:pointer; font-weight:700; }
      </style>
      <div id="map"></div><div class="msg" hidden></div>`;
  }
  set data(d) { this._d = d; this._draw(); }
  connectedCallback() {
    if (this._map) { setTimeout(() => this._map.invalidateSize(), 50); return; }
    loadLeaflet().then(L => { this.L = L; this._init(); this._draw(); })
      .catch(e => { const m = this.shadowRoot.querySelector(".msg"); m.hidden = false; m.textContent = e.message; });
    if (!this._ro) { this._ro = new ResizeObserver(() => this._map && this._map.invalidateSize()); this._ro.observe(this); }
  }
  _init() {
    const L = this.L, el = this.shadowRoot.querySelector("#map");
    const map = this._map = L.map(el, { zoomControl: true, attributionControl: true, zoomSnap: 0.5 });
    map.attributionControl.setPrefix(false);
    // keyless Esri tiles: light grey base, hillshade multiplied in for terrain, place names on top
    const esri = n => `https://server.arcgisonline.com/ArcGIS/rest/services/${n}/MapServer/tile/{z}/{y}/{x}`;
    L.tileLayer(esri("Canvas/World_Light_Gray_Base"), { maxZoom: 18, maxNativeZoom: 16, attribution: "Esri, HERE, Garmin, © OpenStreetMap contributors" }).addTo(map);
    L.tileLayer(esri("Elevation/World_Hillshade"), { maxZoom: 18, maxNativeZoom: 16, opacity: 0.45, className: "hill", attribution: "Hillshade © Esri" }).addTo(map);
    map.createPane("labels").style.zIndex = 380;
    map.getPane("labels").style.pointerEvents = "none";
    L.tileLayer(esri("Canvas/World_Light_Gray_Reference"), { pane: "labels", maxZoom: 18, maxNativeZoom: 16 }).addTo(map);
    map.attributionControl.addAttribution("Fires: NASA FIRMS, fogos.pt/ANEPC · Burnt areas: EFFIS © Copernicus · Risk: IPMA · Wind: Open-Meteo");
    const pane = (name, z, op) => { const p = map.createPane(name); p.style.zIndex = z; if (op != null) p.style.opacity = op; return p; };
    pane("burnt", 340); pane("sector", 345); pane("rings", 350);
    AGES.forEach((a, i) => pane("age" + i, 409 - i, a[2]));
    pane("wind", 615);
    this._layers = L.layerGroup().addTo(map);
    this._renderers = AGES.map((a, i) => L.svg({ pane: "age" + i, padding: 0.5 }));
    const legend = document.createElement("div");
    legend.className = "legend min";
    legend.innerHTML = `<div class="r tog">legend ▴</div><div class="full">
      <div class="r"><span class="ages">${AGES.map(a => `<i style="background:${a[1]};opacity:${Math.max(.35, a[2])}"></i>`).join("")}</span>heat, new → 48 h</div>
      <div class="r"><i style="background:#E0402C"></i>reported fire (fogos.pt)</div>
      <div class="r"><i style="background:#4A3426;opacity:.5;border-radius:3px"></i>burnt this year</div>
      <div class="r"><i style="background:#F07F1A;opacity:.25;border-radius:3px"></i>upwind sector</div>
      <div class="r"><i style="border:2px dashed #E0402C;box-sizing:border-box"></i>alert rings</div></div>`;
    legend.querySelector(".tog").addEventListener("click", () => {
      legend.classList.toggle("min");
      legend.querySelector(".tog").textContent = legend.classList.contains("min") ? "legend ▴" : "legend ▾";
    });
    L.DomEvent.disableClickPropagation(legend);
    this.shadowRoot.appendChild(legend);
  }

  _draw() {
    const d = this._d, L = this.L, map = this._map;
    if (!d || !L || !map) return;
    const g = this._layers;
    g.clearLayers();
    const home = [d.home.lat, d.home.lon], s = d.settings;
    if (!this._fitted) map.setView(home, 10);
    // rings
    const ring = (r, color, dash, weight, label) => {
      const c = L.circle(home, { radius: r * 1000, pane: "rings", color, weight, dashArray: dash, fill: false, interactive: false }).addTo(g);
      if (label) L.tooltip({ permanent: true, direction: "center", className: "ringlbl", pane: "rings", interactive: false })
        .setLatLng([d.home.lat + r / 111.32, d.home.lon]).setContent(`<span style="color:${color}">${label}</span>`).addTo(g);
      return c;
    };
    ring(d.radius_km, "#6B7280", "2 6", 1.2, `${Math.round(d.radius_km)} km`);
    ring(s.watch_km, "#D97706", "1 5", 1.2, `${km(s.watch_km)} km`);
    ring(s.upwind_km, "#E0402C", "6 5", 2, `${km(s.upwind_km)} km`);
    ring(s.close_km, "#C62828", null, 2, "");
    // upwind sector: where a fire would have the wind blowing from it toward home
    const w = d.wind || {};
    if (w.from != null && (w.speed || 0) >= s.min_wind) {
      const pts = [home];
      for (let a = -s.wind_angle; a <= s.wind_angle; a += 3) {
        const b = (w.from + a) * Math.PI / 180, r = s.watch_km;
        pts.push([d.home.lat + r * Math.cos(b) / 111.32, d.home.lon + r * Math.sin(b) / (111.32 * Math.cos(d.home.lat * Math.PI / 180))]);
      }
      L.polygon(pts, { pane: "sector", stroke: false, fillColor: "#F07F1A", fillOpacity: 0.13, interactive: false }).addTo(g);
    }
    // burnt areas
    if (d.burnt && d.burnt.length) {
      L.geoJSON({ type: "FeatureCollection", features: d.burnt }, {
        pane: "burnt", style: { color: "#5B4030", weight: 1, opacity: 0.6, fillColor: "#4A3426", fillOpacity: 0.28 },
        onEachFeature: (f, l) => l.bindPopup(`<b>burnt area</b><br>${esc(f.properties.date || "")}${f.properties.area_ha ? ` · ${Math.round(f.properties.area_ha)} ha` : ""}<br><span style="color:#777">EFFIS © Copernicus</span>`),
      }).addTo(g);
    }
    // satellite heat: circles merged per age band (outline pass then fill pass in one SVG, so shared edges vanish)
    const bands = AGES.map(() => []);
    for (const h of d.hotspots || []) {
      const i = AGES.findIndex(a => h.age_h <= a[0]);
      if (i >= 0) bands[i].push(h);
    }
    bands.forEach((list, i) => {
      if (!list.length) return;
      const r = this._renderers[i], fill = AGES[i][1];
      // each detection is a ~375 m pixel: a 330 m circle merges with its neighbours; a 5 px dot keeps it visible zoomed out
      const shapes = (h, o) => [L.circle([h.lat, h.lon], { renderer: r, radius: 330, ...o }), L.circleMarker([h.lat, h.lon], { renderer: r, ...o, radius: 5 })];
      list.forEach(h => shapes(h, { stroke: true, color: "#7A1A0C", weight: 3, fill: false, interactive: false }).forEach(x => x.addTo(g)));
      list.forEach(h => shapes(h, { stroke: false, fillColor: fill, fillOpacity: 1 }).forEach(x => x.addTo(g).bindPopup(`<b>heat detected</b>${h.test ? " (test)" : ""}<br>${ago(h.age_h)} · ${esc(h.sat)}${h.frp != null ? ` · ${h.frp} MW` : ""}<br>${km(h.distance)} km ${esc(h.dir)} of home${h.upwind ? ", upwind" : ""}<br><a href="https://fogos.pt/" target="_blank" rel="noopener">check fogos.pt</a>`)));
    });
    // reported incidents
    for (const f of d.incidents || []) {
      const cls = f.status_code >= 3 && f.status_code <= 6 ? "active" : f.status_code === 7 ? "resolving" : "ended";
      const icon = L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13],
        html: `<div class="inc ${cls}${f.test ? " test" : ""}"><svg viewBox="0 0 24 24"><path d="${FIRE_PATH}"/></svg></div>` });
      const means = [f.people != null ? `${f.people} people` : "", f.vehicles != null ? `${f.vehicles} vehicles` : "", f.aircraft ? `${f.aircraft} aircraft` : ""].filter(Boolean).join(", ");
      L.marker([f.lat, f.lon], { icon, zIndexOffset: cls === "active" ? 1000 : 0 }).addTo(g).bindPopup(
        `<b>${esc(f.status || "fire")}</b>${f.test ? " (test)" : ""}<br>${esc(f.place || "")}${f.kind ? `<br>${esc(f.kind)}` : ""}${means ? `<br>${esc(means)}` : ""}`
        + `<br>started ${ago(hoursSince(f.time))} · ${km(f.distance)} km ${esc(f.dir)}${f.upwind ? ", upwind" : ""}<br><a href="${esc(f.url)}" target="_blank" rel="noopener">open on fogos.pt</a>`);
    }
    // wind arrows (pointing where the wind blows to)
    for (const p of d.wind_grid || []) {
      if (p.dir == null || Math.hypot(p.lat - d.home.lat, (p.lon - d.home.lon) * 0.75) < 0.02) continue; // keep home clear
      const len = Math.min(34, 14 + p.speed * 0.6);
      const icon = L.divIcon({ className: "", iconSize: [40, 48], iconAnchor: [20, 20],
        html: `<div class="wind"><svg width="40" height="40" viewBox="-20 -20 40 40" style="transform:rotate(${(p.dir + 180) % 360}deg)">
          <line x1="0" y1="${len / 2}" x2="0" y2="${-len / 2 + 5}" stroke="#33415C" stroke-width="2.2" stroke-linecap="round" opacity=".8"/>
          <path d="M0,${-len / 2} L5,${-len / 2 + 8} L-5,${-len / 2 + 8} Z" fill="#33415C" opacity=".8"/></svg><span>${Math.round(p.speed)}</span></div>` });
      L.marker([p.lat, p.lon], { icon, pane: "wind", interactive: false, keyboard: false }).addTo(g);
    }
    // home
    L.marker(home, { icon: L.divIcon({ className: "", iconSize: [14, 14], iconAnchor: [7, 7], html: `<div class="home"></div>` }), zIndexOffset: 2000 })
      .addTo(g).bindTooltip("home", { direction: "top", offset: [0, -8] });
    if (!this._fitted) {
      const r = d.radius_km / 111.32, rl = r / Math.cos(d.home.lat * Math.PI / 180);
      map.fitBounds([[d.home.lat - r, d.home.lon - rl], [d.home.lat + r, d.home.lon + rl]], { padding: [6, 6] });
      this._fitted = true;
    }
  }
}

// ======================================================================
// Full-screen map dialog, attached to the page body so no card layout can clip it
// ======================================================================
class VigiaMapDialog extends HTMLElement {
  static open(hass) {
    let dlg = document.body.querySelector("vigia-map-dialog");
    if (!dlg) { dlg = document.createElement("vigia-map-dialog"); document.body.appendChild(dlg); }
    dlg.show(hass);
  }
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host { position:fixed; inset:0; z-index:9999; display:none; font-family:'Lato',sans-serif; }
        :host([open]) { display:block; }
        .bg { position:absolute; inset:0; background:rgba(0,0,0,.45); }
        .box { position:absolute; inset:max(16px, 3vh) max(16px, 3vw); background:var(--card-background-color, #fff); border-radius:20px; overflow:hidden;
          display:flex; flex-direction:column; box-shadow:0 10px 40px rgba(0,0,0,.35); }
        @media (max-width: 600px) { .box { inset:0; border-radius:0; } }
        .head { display:flex; align-items:center; gap:10px; padding:10px 12px 10px 16px; }
        .t { flex:1; min-width:0; }
        .t1 { font-size:16px; font-weight:800; color:var(--primary-text-color); }
        .t2 { font-size:12px; color:var(--secondary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        button { all:unset; cursor:pointer; width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center;
          color:var(--primary-text-color); background:color-mix(in srgb, var(--primary-text-color) 8%, transparent); }
        button ha-icon { --mdc-icon-size:20px; }
        vigia-map { flex:1; }
      </style>
      <div class="bg"></div>
      <div class="box"><div class="head"><div class="t"><div class="t1">fire map</div><div class="t2"></div></div>
        <button title="close"><ha-icon icon="mdi:close"></ha-icon></button></div><vigia-map></vigia-map></div>`;
    this._onData = () => this._update();
    this._onKey = e => { if (e.key === "Escape") this.hide(); };
    this.shadowRoot.querySelector("button").addEventListener("click", () => this.hide());
    this.shadowRoot.querySelector(".bg").addEventListener("click", () => this.hide());
  }
  show(hass) {
    this.setAttribute("open", "");
    hub.attach(hass, this._onData);
    document.addEventListener("keydown", this._onKey);
    const m = this.shadowRoot.querySelector("vigia-map");
    if (m._map) setTimeout(() => m._map.invalidateSize(), 60);
  }
  hide() { this.removeAttribute("open"); hub.detach(this._onData); document.removeEventListener("keydown", this._onKey); }
  _update() {
    const d = hub.data;
    if (!d) return;
    this.shadowRoot.querySelector("vigia-map").data = d;
    const w = d.wind || {};
    const windTxt = w.speed != null ? `wind ${Math.round(w.speed)} km/h${w.from != null ? " from " + ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(w.from / 45) % 8] : ""}` : "";
    const n = d.counts.incidents, h = d.counts.hotspots;
    this.shadowRoot.querySelector(".t2").textContent = [
      `${n} reported fire${n === 1 ? "" : "s"}, ${h} heat detection${h === 1 ? "" : "s"} (24 h)`, windTxt, `updated ${hhmm(d.computed)}`,
      d.test_mode ? "test mode" : "",
    ].filter(Boolean).join(" · ");
  }
}

// ======================================================================
// The map as a card
// ======================================================================
class VigiaMapCard extends HTMLElement {
  setConfig(c) {
    this._c = { height: 420, ...c };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `<style>:host{display:block}ha-card{overflow:hidden;height:${this._c.height}px}vigia-map{height:100%}</style><ha-card><vigia-map></vigia-map></ha-card>`;
    this._onData = () => { if (hub.data) this.shadowRoot.querySelector("vigia-map").data = hub.data; };
  }
  static getStubConfig() { return {}; }
  getCardSize() { return Math.ceil(this._c.height / 50); }
  getGridOptions() { return { columns: "full", rows: "auto" }; }
  set hass(h) { this._hass = h; if (!this._attached && this.isConnected) { this._attached = true; hub.attach(h, this._onData); } }
  connectedCallback() { if (this._hass && !this._attached) { this._attached = true; hub.attach(this._hass, this._onData); } }
  disconnectedCallback() { hub.detach(this._onData); this._attached = false; }
}

const define = (tag, cls) => { if (!customElements.get(tag)) customElements.define(tag, cls); };
define("vigia-map", VigiaMap);
define("vigia-map-dialog", VigiaMapDialog);
define("vigia-card", VigiaCard);
define("vigia-map-card", VigiaMapCard);
window.customCards = window.customCards || [];
for (const c of [
  { type: "vigia-card", name: "Vigia fire tile", description: "Fire risk, warnings, nearby fires and alert settings. Tap for the fire map." },
  { type: "vigia-map-card", name: "Vigia fire map", description: "Nearby fires, satellite heat, burnt areas and wind on a terrain map." },
]) if (!window.customCards.find(x => x.type === c.type)) window.customCards.push({ ...c, preview: false, documentationURL: "https://github.com/eyeofeska/ha-vigia" });
console.info(`%c VIGIA %c ${VERSION} `, "background:#E0402C;color:#fff;font-weight:700", "background:#333;color:#fff");
})();
