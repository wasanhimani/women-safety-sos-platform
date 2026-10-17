import { APP_NAME, DATA_EVENT, chime, h, icon, toast } from "./ui.js";
import { api, getSession, setSession } from "./api.js";
import * as auth from "./views/auth.js";
import * as home from "./views/home.js";
import * as contacts from "./views/contacts.js";
import * as alerts from "./views/alerts.js";
import * as account from "./views/account.js";

const ROUTES = {
  home: { view: home, label: "SOS", icon: "sos" },
  contacts: { view: contacts, label: "Contacts", icon: "people", badge: "contacts" },
  alerts: { view: alerts, label: "Alerts", icon: "bell", badge: "alerts" },
  account: { view: account, label: "Account", icon: "person" },
};
const POLL_MS = 5000;

const state = freshState();
let cleanup = null;
let pollTimer = null;

function freshState() {
  return { contacts: null, inbox: [], knownAlerts: new Set(), inboxLoaded: false, offline: false };
}

function resetState() {
  Object.assign(state, freshState());
}

const ctx = { state, refresh, startSession, logout, updateBadges };

function currentRoute() {
  const name = location.hash.replace(/^#\/?/, "");
  return ROUTES[name] ? name : "home";
}

function tabBar(active) {
  return h(
    "nav",
    { class: "tabbar", "aria-label": "Main" },
    ...Object.entries(ROUTES).map(([name, route]) =>
      h(
        "a",
        { class: "tab", href: `#/${name}`, "aria-current": name === active ? "page" : null },
        icon(route.icon),
        h("span", {}, route.label),
        route.badge ? h("span", { class: "badge", "data-badge": route.badge, hidden: true }) : null,
      ),
    ),
  );
}

function render() {
  const app = document.getElementById("app");
  if (cleanup) cleanup();
  cleanup = null;
  app.replaceChildren();
  document.title = APP_NAME;
  if (!getSession()) {
    cleanup = auth.mount(app, ctx) || null;
    return;
  }
  const route = currentRoute();
  const main = h("main", { class: "screen", id: "main" });
  const banner = h("div", { class: "offline-banner", id: "offline", role: "status", hidden: !state.offline }, "Can't reach the server. Trying again…");
  app.append(banner, main, tabBar(route));
  cleanup = ROUTES[route].view.mount(main, ctx) || null;
  updateBadges();
  window.scrollTo(0, 0);
}

function setBadge(name, count) {
  const el = document.querySelector(`[data-badge="${name}"]`);
  if (!el) return;
  el.textContent = count > 9 ? "9+" : String(count);
  el.hidden = count === 0;
}

function updateBadges() {
  const unseen = state.inbox.filter((a) => !a.seen).length;
  const requests = state.contacts ? state.contacts.requests_to_me.length : 0;
  setBadge("alerts", unseen);
  setBadge("contacts", requests);
  document.title = unseen ? `(${unseen}) ${APP_NAME}` : APP_NAME;
}

function absorbInbox(inbox) {
  const fresh = inbox.filter((a) => !a.seen && !state.knownAlerts.has(a.delivery_id));
  inbox.forEach((a) => state.knownAlerts.add(a.delivery_id));
  // The first load only learns what already exists. After that, anything new gets a chime.
  if (state.inboxLoaded && fresh.length) {
    chime();
    toast(fresh.length > 1 ? `${fresh.length} new SOS alerts` : `SOS from ${fresh[0].from}`, "alert");
  }
  state.inboxLoaded = true;
  state.inbox = inbox;
}

function setOffline(flag) {
  state.offline = flag;
  const banner = document.getElementById("offline");
  if (banner) banner.hidden = !flag;
}

async function refresh() {
  if (!getSession()) return;
  try {
    const [contactsData, inbox] = await Promise.all([api.contacts(), api.inbox()]);
    state.contacts = contactsData;
    absorbInbox(inbox);
    setOffline(false);
    updateBadges();
  } catch {
    // No connection or the server is busy: keep what we have, say so, and try again on the next tick.
    setOffline(true);
  }
  window.dispatchEvent(new Event(DATA_EVENT));
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    if (!document.hidden) refresh();
  }, POLL_MS);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

function startSession(data) {
  setSession({ token: data.access_token, user: data.user });
  resetState();
  startPolling();
  refresh();
  if (location.hash !== "#/home") location.hash = "#/home";
  else render();
}

function logout() {
  setSession(null);
  stopPolling();
  resetState();
  location.hash = "";
  render();
}

window.addEventListener("hashchange", render);
window.addEventListener("raksha:logout", () => {
  stopPolling();
  resetState();
  render();
  toast("Your session ended. Please log in again.");
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});

render();
if (getSession()) {
  startPolling();
  refresh();
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
