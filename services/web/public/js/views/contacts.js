import { DATA_EVENT, h, toast } from "../ui.js";
import { api } from "../api.js";

export function mount(root, ctx) {
  const requestsBox = h("div");
  const mineBox = h("div");
  const protectingBox = h("div");

  const email = h("input", { id: "contact-email", name: "email", type: "email", required: true, autocomplete: "off", inputmode: "email", placeholder: "friend@example.com" });
  const error = h("p", { class: "error", role: "alert", hidden: true });
  const addButton = h("button", { class: "btn btn-block", type: "submit" }, "Add contact");
  const form = h(
    "form",
    { class: "sheet" },
    h("h2", { class: "sheet-title" }, "Add someone you trust"),
    h(
      "div",
      { class: "row row-stack" },
      h("div", { class: "field" }, h("label", { for: "contact-email" }, "Their email"), email, h("p", { class: "help" }, "They need a Raksha account with this email. They will get a request to accept.")),
      error,
      addButton,
    ),
  );

  root.append(
    h("h1", {}, "Trusted contacts"),
    h("p", { class: "lede" }, "These people get your SOS and your location. Nobody receives anything until they accept."),
    requestsBox,
    mineBox,
    form,
    protectingBox,
  );

  function sheet(title, rows, empty) {
    return h("section", { class: "sheet" }, h("h2", { class: "sheet-title" }, title), rows.length ? rows : h("div", { class: "empty" }, empty));
  }

  function person(c) {
    return h("div", { class: "who" }, h("strong", {}, c.name), h("span", { class: "muted" }, c.email));
  }

  async function act(button, work, done) {
    button.disabled = true;
    try {
      await work();
      toast(done, "ok");
      await ctx.refresh();
    } catch (err) {
      toast(err.message);
      button.disabled = false;
    }
  }

  let shown = "";
  function draw() {
    const c = ctx.state.contacts;
    const signature = JSON.stringify(c);
    if (signature === shown) return;
    shown = signature;
    if (!c) {
      mineBox.replaceChildren(sheet("Your trusted contacts", [], h("p", {}, "Loading…")));
      return;
    }
    requestsBox.replaceChildren(
      ...(c.requests_to_me.length
        ? [
            sheet(
              "Asking you to be their contact",
              c.requests_to_me.map((r) => {
                const accept = h("button", { class: "btn btn-small", type: "button" }, "Accept");
                const decline = h("button", { class: "btn btn-quiet btn-small", type: "button" }, "Decline");
                accept.addEventListener("click", () => act(accept, () => api.acceptContact(r.id), `You will now get alerts from ${r.name}.`));
                decline.addEventListener("click", () => act(decline, () => api.removeContact(r.id), "Request declined."));
                return h("div", { class: "row" }, person(r), h("div", { class: "row-end" }, accept, decline));
              }),
            ),
          ]
        : []),
    );
    mineBox.replaceChildren(
      sheet(
        "Your trusted contacts",
        c.my_contacts.map((m) => {
          const remove = h("button", { class: "btn btn-quiet btn-small", type: "button" }, "Remove");
          remove.addEventListener("click", () => {
            if (confirm(`Remove ${m.name} from your trusted contacts?`)) act(remove, () => api.removeContact(m.id), `${m.name} removed.`);
          });
          const chip = m.status === "accepted" ? h("span", { class: "chip chip-ok" }, "Gets your alerts") : h("span", { class: "chip chip-wait" }, "Waiting to accept");
          return h("div", { class: "row" }, person(m), h("div", { class: "row-end" }, chip, remove));
        }),
        h("p", {}, "Nobody yet. Add a friend, a sibling or a parent below."),
      ),
    );
    protectingBox.replaceChildren(
      ...(c.protecting.length
        ? [sheet("You get alerts from", c.protecting.map((p) => h("div", { class: "row" }, person(p))))]
        : []),
    );
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = email.value.trim();
    if (!value) return;
    addButton.disabled = true;
    error.hidden = true;
    try {
      const added = await api.addContact(value);
      toast(`Request sent to ${added.name}. They need to accept it.`, "ok");
      email.value = "";
      await ctx.refresh();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    } finally {
      addButton.disabled = false;
    }
  });

  window.addEventListener(DATA_EVENT, draw);
  draw();
  ctx.refresh();
  return () => window.removeEventListener(DATA_EVENT, draw);
}
