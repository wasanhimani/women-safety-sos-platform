import { DATA_EVENT, dismissAlertToasts, fullTime, h, plural, timeAgo, toast } from "../ui.js";
import { api } from "../api.js";

let lastTab = "received";

export function mount(root, ctx) {
  let tab = lastTab;
  let sent = null;
  const list = h("div");

  let shown = "";
  const buttons = {};
  const tabButton = (value, text) => {
    buttons[value] = h("button", { type: "button", role: "tab", onclick: () => { tab = lastTab = value; if (value === "sent") loadSent(); draw(true); } }, text);
    return buttons[value];
  };
  const seg = h("div", { class: "seg", role: "tablist" }, tabButton("received", "Received"), tabButton("sent", "Sent"));

  dismissAlertToasts();
  root.append(h("h1", {}, "Alerts"), seg, list);

  async function loadSent() {
    try {
      sent = await api.sent();
      if (tab === "sent") draw();
    } catch {
      /* keep the old list */
    }
  }

  async function markSeen(item) {
    if (item.seen) return;
    try {
      await api.markSeen(item.delivery_id);
      item.seen = true;
      ctx.updateBadges();
      draw();
    } catch (err) {
      toast(err.message);
    }
  }

  function received() {
    const inbox = ctx.state.inbox;
    if (!inbox.length) return h("section", { class: "sheet" }, h("div", { class: "empty" }, h("p", {}, "No alerts for you. When someone who trusts you presses SOS, it shows up here with their location.")));
    return h(
      "section",
      { class: "sheet" },
      ...inbox.map((a) =>
        h(
          "article",
          { class: `alert${a.seen ? "" : " alert-unseen"}` },
          h("div", { class: "alert-head" }, h("strong", {}, a.seen ? null : h("span", { class: "sr-only" }, "Unseen. "), a.from), h("time", { datetime: a.sent_at, title: fullTime(a.sent_at) }, timeAgo(a.sent_at))),
          h("p", { class: "alert-msg" }, a.message),
          h(
            "div",
            { class: "alert-actions" },
            h("a", { class: "btn btn-small", href: a.maps_url, target: "_blank", rel: "noopener noreferrer", onclick: () => markSeen(a) }, "Open location in Maps"),
            a.seen ? null : h("button", { class: "btn btn-quiet btn-small", type: "button", onclick: () => markSeen(a) }, "Mark as seen"),
          ),
        ),
      ),
    );
  }

  function sentList() {
    if (sent === null) return h("section", { class: "sheet" }, h("div", { class: "empty" }, h("p", {}, "Loading…")));
    if (!sent.length) return h("section", { class: "sheet" }, h("div", { class: "empty" }, h("p", {}, "You have not sent any alerts.")));
    return h(
      "section",
      { class: "sheet" },
      ...sent.map((a) =>
        h(
          "article",
          { class: "alert" },
          h("div", { class: "alert-head" }, h("strong", {}, a.message), h("time", { datetime: a.sent_at, title: fullTime(a.sent_at) }, timeAgo(a.sent_at))),
          h(
            "p",
            { class: "alert-facts" },
            a.delivered_to === 0 ? h("span", {}, "Nobody was notified. You had no accepted contacts then.") : [h("span", {}, `Delivered to ${plural(a.delivered_to, "contact", "contacts")}`), h("span", {}, a.seen_by ? `Seen by ${a.seen_by}` : "Not seen yet")],
          ),
        ),
      ),
    );
  }

  function draw(force = false) {
    // Redraw only when something changed, so a tap is never lost to a refresh happening under the finger.
    const signature = `${tab}|${Math.floor(Date.now() / 60000)}|${JSON.stringify(tab === "received" ? ctx.state.inbox : sent)}`;
    if (!force && signature === shown) return;
    shown = signature;
    Object.entries(buttons).forEach(([value, el]) => el.setAttribute("aria-selected", String(tab === value)));
    list.replaceChildren(tab === "received" ? received() : sentList());
  }

  const onData = () => {
    if (tab === "received") draw();
  };
  window.addEventListener(DATA_EVENT, onData);
  const timer = setInterval(() => {
    if (tab === "sent" && !document.hidden) loadSent();
  }, 6000);

  draw();
  ctx.refresh();
  if (tab === "sent") loadSent();
  return () => {
    window.removeEventListener(DATA_EVENT, onData);
    clearInterval(timer);
  };
}
