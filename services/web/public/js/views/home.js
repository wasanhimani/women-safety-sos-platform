import { DATA_EVENT, h, plural, s, vibrate } from "../ui.js";
import { api, getSession } from "../api.js";

const HOLD_MS = 2000;
const RADIUS = 46;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
const DEMO_LOCATION = { latitude: 28.6692, longitude: 77.4538 }; // Ghaziabad, for testing without GPS
const LAST_KEY = "raksha.lastLocation";
export const DEMO_KEY = "raksha.demoLocation";

function readLast() {
  try {
    return JSON.parse(localStorage.getItem(LAST_KEY));
  } catch {
    return null;
  }
}

/** Resolves to {ok:true, latitude, longitude, ...flags} or {ok:false, message}. */
function getLocation() {
  try {
    if (localStorage.getItem(DEMO_KEY) === "1") return Promise.resolve({ ok: true, ...DEMO_LOCATION, demo: true });
  } catch {
    /* ignore */
  }
  const fallback = (message) => {
    const last = readLast();
    if (last && Date.now() - last.at < 30 * 60 * 1000) {
      return { ok: true, latitude: last.latitude, longitude: last.longitude, approximate: true };
    }
    return { ok: false, message };
  };
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(fallback("This device cannot share its location."));
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const loc = { latitude: pos.coords.latitude, longitude: pos.coords.longitude };
        try {
          localStorage.setItem(LAST_KEY, JSON.stringify({ ...loc, at: Date.now() }));
        } catch {
          /* ignore */
        }
        resolve({ ok: true, ...loc });
      },
      (err) =>
        resolve(
          fallback(
            err.code === 1
              ? "Location is blocked for this site. Allow location in your browser settings, then try again."
              : "Could not get your location. Move to an open area and try again.",
          ),
        ),
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 30000 },
    );
  });
}

export function mount(root, ctx) {
  const user = getSession().user;
  let phase = "idle"; // idle | holding | working | sent | failed
  let raf = 0;
  let holdStart = 0;
  let startedAt = 0;
  let pollTimer = 0;
  let alive = true;

  const progress = s("circle", { class: "progress", cx: 50, cy: 50, r: RADIUS, "stroke-dasharray": CIRCUMFERENCE });
  progress.style.strokeDashoffset = String(CIRCUMFERENCE);
  const ring = s("svg", { class: "ring", viewBox: "0 0 100 100", "aria-hidden": "true" }, s("circle", { class: "track", cx: 50, cy: 50, r: RADIUS }), progress);
  const label = h("span", { class: "sos-label" }, "SOS");
  const sub = h("span", { class: "sos-sub" }, "Hold to send");
  const button = h("button", { class: "sos", type: "button", "data-phase": "idle", "aria-describedby": "sos-status" }, label, sub);
  const ripples = [h("span", { class: "ripple" }), h("span", { class: "ripple r2" })];
  const status = h("p", { class: "status", id: "sos-status" });
  const hint = h("p", { class: "hint" }, "Press and hold for 2 seconds.");
  const result = h("div", { class: "result", hidden: true });

  root.append(
    h("p", { class: "hello" }, `Hi, ${user.name.trim().split(/\s+/)[0]}`),
    h("div", { class: "sos-stage" }, h("div", { class: "sos-wrap" }, ring, ...ripples, button)),
    status,
    hint,
    result,
  );

  const fill = (...nodes) => result.replaceChildren(...nodes.filter(Boolean));

  function setPhase(next, big, small) {
    ring.setAttribute("class", `ring ring-${next}`);
    status.hidden = next === "working" || next === "sent" || next === "failed";
    phase = next;
    button.dataset.phase = next;
    label.textContent = big;
    sub.textContent = small;
  }

  function acceptedCount() {
    const c = ctx.state.contacts;
    return c ? c.my_contacts.filter((x) => x.status === "accepted").length : null;
  }

  function refreshStatus() {
    if (phase !== "idle") return;
    const n = acceptedCount();
    const c = ctx.state.contacts;
    status.replaceChildren();
    button.disabled = n === 0;
    hint.hidden = n === 0;
    if (n === null) status.append("Checking your trusted contacts…");
    else if (n === 0) {
      const waiting = c.my_contacts.length > 0;
      status.append(
        waiting ? "Your contacts have not accepted yet. " : "You have no trusted contacts yet. ",
        h("a", { class: "inline-link", href: "#/contacts" }, waiting ? "See your requests" : "Add a contact"),
      );
    } else status.append(`${plural(n, "trusted contact", "trusted contacts")} will get your alert.`);
  }

  function resetRing(animated) {
    progress.classList.toggle("release", animated);
    progress.style.strokeDashoffset = String(CIRCUMFERENCE);
  }

  function reset() {
    clearInterval(pollTimer);
    ripples.forEach((r) => r.classList.remove("go"));
    result.hidden = true;
    result.replaceChildren();
    resetRing(false);
    setPhase("idle", "SOS", "Hold to send");
    refreshStatus();
  }

  function startHold() {
    if (phase !== "idle" || button.disabled) return;
    holdStart = performance.now();
    setPhase("holding", "SOS", "Keep holding");
    progress.classList.remove("release");
    vibrate(25);
    raf = requestAnimationFrame(tick);
  }

  function tick() {
    const t = Math.min(1, (performance.now() - holdStart) / HOLD_MS);
    progress.style.strokeDashoffset = String(CIRCUMFERENCE * (1 - t));
    if (t >= 1) complete();
    else raf = requestAnimationFrame(tick);
  }

  function cancelHold() {
    if (phase !== "holding") return;
    cancelAnimationFrame(raf);
    const heldFor = performance.now() - holdStart;
    resetRing(true);
    setPhase("idle", "SOS", "Hold to send");
    if (heldFor < 400) {
      hint.classList.add("hint-strong");
      hint.textContent = "Keep pressing for 2 seconds to send.";
      setTimeout(() => {
        hint.classList.remove("hint-strong");
        hint.textContent = "Press and hold for 2 seconds.";
      }, 2500);
    }
  }

  async function complete() {
    vibrate([80, 40, 80]);
    hint.hidden = true;
    startedAt = Date.now();
    await attempt();
  }

  async function attempt() {
    result.hidden = true;
    setPhase("working", "…", "Finding you");
    const loc = await getLocation();
    if (!loc.ok) return fail(loc.message, true);
    setPhase("working", "…", "Sending");
    try {
      await api.sos(loc.latitude, loc.longitude, "I need help");
    } catch (err) {
      return fail(err.message, false);
    }
    succeed(loc);
  }

  function fail(message, isLocation) {
    if (!alive) return;
    resetRing(true);
    setPhase("failed", "Not sent", "Your alert did not go out");
    fill(
      h("p", { class: "error", role: "alert" }, message),
      isLocation ? h("p", { class: "help" }, "Testing on a computer? Turn on Demo location in Account.") : null,
      h("button", { class: "btn btn-block", type: "button", onclick: attempt }, "Try again"),
      h("button", { class: "btn btn-quiet btn-block", type: "button", onclick: reset }, "Cancel"),
    );
    result.hidden = false;
  }

  function succeed(loc) {
    if (!alive) return;
    setPhase("sent", "Sent", "Alert is on its way");
    vibrate([60, 40, 60, 40, 120]);
    ripples.forEach((r) => {
      r.classList.remove("go");
      void r.offsetWidth;
      r.classList.add("go");
    });
    const delivery = h("p", { id: "delivery" }, "Delivering to your trusted contacts…");
    fill(
      h("h2", {}, "Your contacts are being told"),
      delivery,
      loc.approximate ? h("p", { class: "help" }, "A fresh location was not available, so your last known location was used.") : null,
      loc.demo ? h("p", { class: "help" }, "Demo location used.") : null,
      h("button", { class: "btn btn-block", type: "button", onclick: reset }, "Send another alert"),
      h("a", { class: "btn btn-quiet btn-block", href: "#/alerts" }, "See alert history"),
    );
    result.hidden = false;
    let polls = 0;
    pollTimer = setInterval(async () => {
      if (!alive || ++polls > 40) return clearInterval(pollTimer);
      try {
        const mine = (await api.sent()).find((a) => Date.parse(a.sent_at) >= startedAt - 15000);
        if (!mine) return;
        delivery.textContent =
          mine.delivered_to === 0
            ? "Saved, but none of your contacts had accepted yet."
            : `Delivered to ${plural(mine.delivered_to, "contact", "contacts")}. ${mine.seen_by ? `Seen by ${mine.seen_by}.` : "Nobody has opened it yet."}`;
      } catch {
        /* keep trying */
      }
    }, 3000);
  }

  button.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    try {
      button.setPointerCapture(event.pointerId);
    } catch {
      /* not critical */
    }
    startHold();
  });
  ["pointerup", "pointercancel", "pointerleave", "blur"].forEach((name) => button.addEventListener(name, cancelHold));
  button.addEventListener("keydown", (event) => {
    if ((event.key === " " || event.key === "Enter") && !event.repeat) {
      event.preventDefault();
      startHold();
    }
  });
  button.addEventListener("keyup", (event) => {
    if (event.key === " " || event.key === "Enter") cancelHold();
  });
  button.addEventListener("contextmenu", (event) => event.preventDefault());
  button.addEventListener("click", (event) => event.preventDefault());

  window.addEventListener(DATA_EVENT, refreshStatus);
  refreshStatus();

  return () => {
    alive = false;
    cancelAnimationFrame(raf);
    clearInterval(pollTimer);
    window.removeEventListener(DATA_EVENT, refreshStatus);
  };
}
