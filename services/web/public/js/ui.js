// Small helpers for building the screens. Everything user-supplied goes in as text,
// never as HTML, so a malicious name or message cannot inject markup.
export const APP_NAME = "Raksha";
export const DATA_EVENT = "raksha:data";

const SVG_NS = "http://www.w3.org/2000/svg";

function apply(el, attrs) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
}

function add(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  apply(el, attrs);
  add(el, children);
  return el;
}

export function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  apply(el, attrs);
  add(el, children);
  return el;
}

const ICONS = {
  sos: ["M12 3l7 3v5.5c0 4.4-3 7.9-7 9.5-4-1.6-7-5.1-7-9.5V6z", "M9 12l2.2 2.2L15.5 10"],
  people: [
    "M9 11a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z",
    "M3 20c0-3.1 2.7-5.5 6-5.5s6 2.4 6 5.5",
    "M16.5 11.2a2.8 2.8 0 1 0 0-5.6",
    "M17.5 14.6c2.2.3 3.9 2.3 3.9 5",
  ],
  bell: ["M6 17v-6a6 6 0 1 1 12 0v6l1.5 2h-15z", "M10 21h4"],
  person: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"],
};

export function icon(name) {
  return s(
    "svg",
    { viewBox: "0 0 24 24", "aria-hidden": "true", fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" },
    ...ICONS[name].map((d) => s("path", { d })),
  );
}

export function logo() {
  return s(
    "svg",
    { viewBox: "0 0 48 48", "aria-hidden": "true", fill: "none" },
    s("circle", { cx: 24, cy: 24, r: 21, stroke: "#E0193C", "stroke-width": 2.5 }),
    s("circle", { cx: 24, cy: 24, r: 14.5, stroke: "#E0193C", "stroke-width": 2.5 }),
    s("circle", { cx: 24, cy: 24, r: 8, fill: "#E0193C" }),
  );
}

export function timeAgo(iso) {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function fullTime(iso) {
  return new Date(iso).toLocaleString();
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

export function toast(message, kind = "info") {
  const box = document.getElementById("toasts");
  if (!box) return;
  if (kind === "alert") {
    // An incoming SOS stays on screen until it is tapped. It must never disappear unnoticed.
    const el = h(
      "button",
      { class: "toast toast-alert", type: "button", role: "alert", onclick: () => { el.remove(); location.hash = "#/alerts"; } },
      h("span", {}, message),
      h("span", { class: "toast-sub" }, "Tap to see where they are"),
    );
    box.append(el);
    return;
  }
  const el = h("div", { class: `toast toast-${kind}` }, message);
  box.append(el);
  setTimeout(() => el.remove(), 5000);
}

export function dismissAlertToasts() {
  document.querySelectorAll(".toast-alert").forEach((el) => el.remove());
}

export function vibrate(pattern) {
  try {
    if (navigator.vibrate) navigator.vibrate(pattern);
  } catch {
    /* not supported */
  }
}

let audio;
/** Three short tones, for when an alert arrives. Browsers only allow sound after the person has tapped something. */
export function chime() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx) {
      audio = audio || new Ctx();
      const start = audio.currentTime;
      [880, 660, 880].forEach((freq, i) => {
        const osc = audio.createOscillator();
        const gain = audio.createGain();
        const t = start + i * 0.22;
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.2);
        osc.connect(gain).connect(audio.destination);
        osc.start(t);
        osc.stop(t + 0.22);
      });
    }
  } catch {
    /* no sound available */
  }
  vibrate([200, 100, 200]);
}
