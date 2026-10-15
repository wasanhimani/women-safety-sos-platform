import { h } from "../ui.js";
import { getSession } from "../api.js";
import { DEMO_KEY } from "./home.js";

export function mount(root, ctx) {
  const user = getSession().user;
  let demo = false;
  try {
    demo = localStorage.getItem(DEMO_KEY) === "1";
  } catch {
    /* ignore */
  }

  const toggle = h("input", { type: "checkbox", role: "switch", checked: demo });
  toggle.checked = demo;
  toggle.addEventListener("change", () => {
    try {
      localStorage.setItem(DEMO_KEY, toggle.checked ? "1" : "0");
    } catch {
      /* ignore */
    }
  });

  root.append(
    h("h1", {}, "Account"),
    h(
      "section",
      { class: "sheet" },
      h("div", { class: "row" }, h("div", { class: "who" }, h("span", { class: "muted" }, "Name"), h("strong", {}, user.name))),
      h("div", { class: "row" }, h("div", { class: "who" }, h("span", { class: "muted" }, "Email"), h("strong", {}, user.email))),
    ),
    h(
      "section",
      { class: "sheet" },
      h("h2", { class: "sheet-title" }, "For testing"),
      h(
        "div",
        { class: "row" },
        h("label", { class: "switch" }, toggle, h("span", { class: "track" }), h("span", { class: "switch-text" }, h("strong", {}, "Demo location"), h("span", { class: "muted" }, "Sends a fixed location in Ghaziabad. Use it on a computer without GPS."))),
      ),
    ),
    h("button", { class: "btn btn-danger btn-block", type: "button", onclick: () => ctx.logout() }, "Log out"),
  );
  return () => {};
}
