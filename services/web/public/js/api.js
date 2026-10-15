// Talks to the backend through /api (nginx forwards it to the sos-api service).
const KEY = "raksha.session";
const NO_CONNECTION = "Can't reach the server. Check your internet connection and try again.";

function load() {
  try {
    return JSON.parse(localStorage.getItem(KEY));
  } catch {
    return null;
  }
}

let session = load();

export const getSession = () => session;

export function setSession(value) {
  session = value;
  try {
    if (value) localStorage.setItem(KEY, JSON.stringify(value));
    else localStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the person will just have to log in again next time */
  }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function messageFor(status, body) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (status === 422) return "Some details are not valid. Check the fields and try again.";
  if (status === 429) return "Too many attempts. Wait a minute and try again.";
  if (status >= 500) return "The server had a problem. Please try again.";
  return "Something went wrong. Please try again.";
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function readJson(res) {
  const text = await res.text();
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return null;
  }
}

async function request(method, path, body, { retries = 0 } = {}) {
  for (let attempt = 0; ; attempt++) {
    let res;
    let data;
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10000);
      try {
        const headers = {};
        if (body !== undefined) headers["content-type"] = "application/json";
        if (session) headers.Authorization = `Bearer ${session.token}`;
        res = await fetch(`/api${path}`, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: controller.signal,
        });
        data = await readJson(res);
      } finally {
        clearTimeout(timer);
      }
    } catch {
      if (attempt < retries) {
        await sleep(600 * 2 ** attempt);
        continue;
      }
      throw new ApiError(0, NO_CONNECTION);
    }
    if (res.ok) return data;
    if (res.status >= 500 && attempt < retries) {
      await sleep(600 * 2 ** attempt);
      continue;
    }
    if (res.status === 401 && session) {
      setSession(null);
      window.dispatchEvent(new Event("raksha:logout"));
      throw new ApiError(401, "Your session ended. Please log in again.");
    }
    throw new ApiError(res.status, messageFor(res.status, data));
  }
}

export const api = {
  signup: (name, email, password) => request("POST", "/auth/signup", { name, email, password }),
  login: (email, password) => request("POST", "/auth/login", { email, password }),
  contacts: () => request("GET", "/contacts"),
  addContact: (email) => request("POST", "/contacts", { email }),
  acceptContact: (id) => request("POST", `/contacts/${id}/accept`, {}),
  removeContact: (id) => request("DELETE", `/contacts/${id}`),
  // The SOS request retries on network errors and server errors. A retry can very rarely
  // produce a duplicate alert, which is the safer mistake than losing one.
  sos: (latitude, longitude, message) => request("POST", "/sos", { latitude, longitude, message }, { retries: 3 }),
  inbox: () => request("GET", "/inbox"),
  markSeen: (id) => request("POST", `/inbox/${id}/seen`, {}),
  sent: () => request("GET", "/alerts/sent"),
};
