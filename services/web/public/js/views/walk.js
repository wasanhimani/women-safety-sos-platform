import { DATA_EVENT, fmtClock, fmtTime, h, secondsLeft, toast, walkPhase } from "../ui.js";
import { api } from "../api.js";
import { getLocation } from "../location.js";

const PRESETS = [15, 30, 45, 60];

export function mount(root, ctx) {
  const body = h("div");
  root.append(h("h1", {}, "Walk home timer"), body);
  let shown = ""; // which screen is on show: "start" or "walk-<id>"
  let paint = () => {};

  const acceptedCount = () => {
    const c = ctx.state.contacts;
    return c ? c.my_contacts.filter((x) => x.status === "accepted").length : null;
  };

  function drawStart() {
    shown = "start";
    paint = () => {};
    let minutes = 30;
    const note = h("input", { id: "walk-note", type: "text", maxlength: 100, autocomplete: "off", placeholder: "For example: campus to home" });
    const custom = h("input", { id: "walk-custom", type: "number", min: 1, max: 240, inputmode: "numeric", placeholder: "Other" });
    const choices = PRESETS.map((m) => h("button", { type: "button", class: "choice", "aria-pressed": String(m === minutes), onclick: () => pick(m) }, `${m} min`));
    function pick(m) {
      minutes = m;
      custom.value = "";
      choices.forEach((b, i) => b.setAttribute("aria-pressed", String(PRESETS[i] === m)));
    }
    custom.addEventListener("input", () => {
      const v = parseInt(custom.value, 10);
      if (v >= 1 && v <= 240) {
        minutes = v;
        choices.forEach((b) => b.setAttribute("aria-pressed", "false"));
      }
    });
    const error = h("p", { class: "error", role: "alert", hidden: true });
    const start = h("button", { class: "btn btn-block", type: "button" }, "Start walk");
    const n = acceptedCount();
    if (n === 0) start.disabled = true;

    start.addEventListener("click", async () => {
      start.disabled = true;
      error.hidden = true;
      start.textContent = "Finding you…";
      const loc = await getLocation();
      const fail = (message) => {
        error.textContent = message;
        error.hidden = false;
        start.disabled = false;
        start.textContent = "Start walk";
      };
      if (!loc.ok) return fail(loc.message);
      try {
        await api.startWalk(minutes, note.value.trim(), loc.latitude, loc.longitude);
        await ctx.refresh();
        toast("Walk started. Your contacts are alerted if you don't check in.", "ok");
      } catch (err) {
        fail(err.message);
      }
    });

    body.replaceChildren(
      h("p", { class: "lede" }, "Set a time. If you have not tapped I'm safe by then, your trusted contacts are alerted with your last known location."),
      h(
        "section",
        { class: "sheet" },
        h("h2", { class: "sheet-title" }, "How long will you be?"),
        h(
          "div",
          { class: "row row-stack" },
          h("div", { class: "choices" }, ...choices),
          h("div", { class: "field" }, h("label", { for: "walk-custom" }, "Or type your own minutes"), custom),
          h("div", { class: "field" }, h("label", { for: "walk-note" }, "Where are you going? (optional)"), note),
          n === 0 ? h("p", { class: "error" }, "You need a trusted contact who has accepted first. ", h("a", { class: "inline-link", href: "#/contacts" }, "Add a contact")) : null,
          error,
          start,
        ),
      ),
      h("p", { class: "help" }, "The countdown runs on our server, not on your phone. If your phone dies or loses signal, your contacts are still alerted."),
    );
  }

  function drawWalk(w) {
    shown = `walk-${w.id}`;
    const chip = h("span", { class: "chip" });
    const clock = h("p", { class: "walk-clock" });
    const caption = h("p", { class: "walk-caption" });
    const safe = h("button", { class: "btn btn-block", type: "button" }, "I'm safe");
    const more = h("button", { class: "btn btn-quiet btn-block", type: "button" }, "Add 10 minutes");
    const cancel = h("button", { class: "btn btn-danger btn-block", type: "button" }, "Cancel walk");
    const help = h("p", { class: "help" }, "You can close the app. The countdown runs on our server, so your contacts are alerted even if your phone dies. Your location is updated while the app is open.");
    const wrap = h(
      "section",
      { class: "walk-card" },
      h("div", { class: "walk-top" }, chip),
      clock,
      caption,
      safe,
      more,
      cancel,
      help,
    );

    safe.addEventListener("click", async () => {
      safe.disabled = true;
      try {
        const result = await api.walkSafe(w.id);
        await ctx.refresh();
        toast(result.contacts_told ? "Your contacts were told you are safe." : "Glad you are safe. Walk ended.", "ok");
      } catch (err) {
        toast(err.message);
        safe.disabled = false;
      }
    });
    more.addEventListener("click", async () => {
      more.disabled = true;
      try {
        await api.extendWalk(w.id, 10);
        await ctx.refresh();
        toast("Added 10 minutes.", "ok");
      } catch (err) {
        toast(err.message);
      }
      more.disabled = false;
    });
    cancel.addEventListener("click", async () => {
      if (!confirm("Cancel this walk? Your contacts will not be alerted.")) return;
      try {
        await api.cancelWalk(w.id);
        await ctx.refresh();
      } catch (err) {
        toast(err.message);
      }
    });

    paint = () => {
      const cur = ctx.state.walk;
      if (!cur) return;
      const phase = walkPhase(cur);
      wrap.dataset.phase = phase;
      chip.className = `chip ${phase === "active" ? "chip-ok" : "chip-alert"}`;
      chip.textContent = { active: "Walking", overdue: "Time is up", alerted: "Alert sent" }[phase];
      more.hidden = cancel.hidden = phase !== "active";
      help.hidden = phase === "alerted";
      if (phase === "active") {
        clock.textContent = fmtClock(secondsLeft(cur));
        caption.textContent = `Check in by ${fmtTime(cur.due_at)}${cur.note ? `, ${cur.note}` : ""}`;
        safe.textContent = "I'm safe";
      } else if (phase === "overdue") {
        clock.textContent = fmtClock(cur.seconds_to_alert - (Date.now() - cur.fetchedAt) / 1000);
        caption.textContent = "Tap I'm safe. If you don't, your contacts are alerted when this reaches zero.";
        safe.textContent = "I'm safe";
      } else {
        clock.textContent = "Contacts alerted";
        caption.textContent = `They were alerted at ${fmtTime(cur.alerted_at)} with your last known location.`;
        safe.textContent = "I'm safe now";
      }
    };
    body.replaceChildren(wrap);
    paint();
  }

  function draw() {
    const w = ctx.state.walk;
    if (!w) {
      if (shown !== "start") drawStart();
    } else if (shown !== `walk-${w.id}`) drawWalk(w);
    paint();
  }

  window.addEventListener(DATA_EVENT, draw);
  const timer = setInterval(() => paint(), 1000);
  draw();
  ctx.refresh();
  return () => {
    window.removeEventListener(DATA_EVENT, draw);
    clearInterval(timer);
  };
}
