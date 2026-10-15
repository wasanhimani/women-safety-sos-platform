import { APP_NAME, h, logo } from "../ui.js";
import { api } from "../api.js";

export function mount(root, ctx) {
  let mode = "login";

  function field(id, label, attrs, help) {
    return h(
      "div",
      { class: "field" },
      h("label", { for: id }, label),
      h("input", { id, name: id, required: true, ...attrs }),
      help ? h("p", { class: "help" }, help) : null,
    );
  }

  function draw() {
    const isLogin = mode === "login";
    const error = h("p", { class: "error", role: "alert", hidden: true });
    const submit = h("button", { class: "btn btn-block", type: "submit" }, isLogin ? "Log in" : "Create account");
    const form = h(
      "form",
      { class: "form" },
      isLogin ? null : field("name", "Your name", { type: "text", autocomplete: "name", maxlength: 80 }),
      field("email", "Email", { type: "email", autocomplete: "email", inputmode: "email" }),
      field(
        "password",
        "Password",
        { type: "password", autocomplete: isLogin ? "current-password" : "new-password", minlength: isLogin ? 1 : 8, maxlength: 72 },
        isLogin ? null : "At least 8 characters.",
      ),
      error,
      submit,
    );

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submit.disabled) return;
      const data = new FormData(form);
      submit.disabled = true;
      submit.textContent = isLogin ? "Logging in…" : "Creating account…";
      error.hidden = true;
      try {
        const result = isLogin
          ? await api.login(data.get("email"), data.get("password"))
          : await api.signup(String(data.get("name")).trim(), data.get("email"), data.get("password"));
        ctx.startSession(result);
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        submit.disabled = false;
        submit.textContent = isLogin ? "Log in" : "Create account";
      }
    });

    const tab = (value, text) =>
      h("button", { type: "button", role: "tab", "aria-selected": String(mode === value), onclick: () => { mode = value; draw(); } }, text);

    root.replaceChildren(
      h(
        "main",
        { class: "auth" },
        h("div", { class: "brand" }, logo(), h("span", {}, APP_NAME)),
        h("h1", {}, "Send your location to the people you trust, in seconds."),
        h("p", { class: "lede" }, "Hold one button. The contacts you choose see where you are and that you need help."),
        h("div", { class: "seg", role: "tablist" }, tab("login", "Log in"), tab("signup", "Create account")),
        form,
      ),
    );
    const first = root.querySelector("input");
    if (first && mode === "signup") first.focus();
  }

  draw();
  return () => {};
}
