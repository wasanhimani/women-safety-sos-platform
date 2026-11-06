// Shared by the SOS button and the walk timer.
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
export function getLocation() {
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

