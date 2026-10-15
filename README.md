# Women Safety SOS Platform

A backend for a personal-safety app. A user presses an SOS button; their trusted contacts see the alert
with a live map link. The design goal is that **an alert is never lost**, even when parts of the system fail.

```
 phone/browser ──▶ web (nginx) ──/api──▶ sos-api ──▶ Redis Stream ──▶ notifier ──▶ PostgreSQL
 (installable app)    serves the app        │         (durable queue)      │            ▲
                      adds security         │                              │            │
                      headers               └── /api/inbox, /contacts ─────┴── reads ───┘
                                            └── /metrics ──▶ Prometheus ──▶ Grafana
```

## Design decisions
- **Queue between API and notifier.** The API answers `202` as soon as the alert is in a durable Redis Stream.
  If the notifier is down, alerts wait and are delivered on recovery (tested: both notifier pods deleted during a 20-alert burst, 0 lost).
- **SOS works even if the database is down.** `/sos` only needs a valid signed token and the queue.
- **Safe retries.** Alerts are saved with their stream id as a unique key, so a retried alert never creates duplicates.
  Malformed messages go to a dead-letter stream instead of retrying forever.
- **Consent for contacts.** Naming someone a trusted contact creates a request; alerts flow only after they accept.
- **Security basics.** bcrypt password hashes, signed expiring tokens, rate-limited login/sign-up/SOS, secrets generated per
  cluster and never stored in git, containers run as non-root, images scanned by Trivy in CI.

## Services
| Service | What it does |
|---|---|
| `web` (nginx + plain JavaScript) | the installable phone-friendly app: SOS button, contacts, alerts. No framework, no build step, no third-party scripts |
| `sos-api` (FastAPI) | accounts, contacts, SOS button, inbox, alert history, `/metrics` |
| `notifier` | reads the queue, saves each alert and delivers it to the sender's accepted contacts |
| Redis | alert queue (Redis Streams, consumer group) |
| PostgreSQL | users, contacts, alerts, deliveries |
| Prometheus / Grafana | metrics; SLO rules in `monitoring/slo-rules.yml` |

## API
| Method and path | Purpose |
|---|---|
| `POST /auth/signup`, `POST /auth/login` | create account / get a token |
| `POST /contacts`, `GET /contacts`, `POST /contacts/{id}/accept`, `DELETE /contacts/{id}` | trusted contacts |
| `POST /sos` | press the SOS button (needs login) |
| `GET /inbox`, `POST /inbox/{id}/seen` | alerts sent to me, mark as seen |
| `GET /alerts/sent` | my alert history, with how many contacts received and saw each |

Interactive docs: `/docs`.

## Run the tests
```bash
pip install -r requirements-dev.txt
pytest -q
```

## Run locally with Docker Compose
```bash
docker compose up --build
```
API docs at http://localhost:8000/docs, Prometheus at http://localhost:9090.

## Run on Kubernetes (kind)
```bash
kind create cluster --name sos
bash scripts/deploy-local.sh     # builds images, creates secrets, deploys everything
bash scripts/open-app.sh         # opens the app at http://localhost:8080
bash scripts/demo.sh             # or: scripted run of the whole story in the terminal
```
To try it with two people, open the app in a normal window (Alice) and a private window (Bob).
On a computer without GPS, switch on **Account > Demo location** before pressing SOS.

## The app
- Hold the SOS button for 2 seconds (a quick tap does nothing, so pocket touches can't send alerts).
- The SOS request retries automatically if the connection drops; a retry can rarely duplicate an alert, which is safer than losing one.
- Incoming alerts show as a notice that stays until tapped, plus a badge, a sound and a vibration.
- Built to be readable under stress: Atkinson Hyperlegible typeface, large touch targets, high-contrast colours, keyboard operable.
- Security: strict Content-Security-Policy (only its own files may run), all user text inserted as text (never HTML), session token kept on the device.
- Tested in a real browser (Chrome) against the real stack: two users, contact request and accept, hold-to-send, live delivery, location-denied failure, no CSP violations.

## CI
GitHub Actions runs the tests, builds all three images and scans them with Trivy; HIGH/CRITICAL findings fail the build.

## Status and roadmap
- [x] Alert pipeline, accounts, contacts, inbox, history
- [x] Docker, Kubernetes manifests (replicas, probes, rolling updates, pod disruption budget), CI with security scan
- [x] Chaos test: all notifier pods deleted during a burst of 20 alerts, none lost
- [x] Mobile-friendly installable web app (SOS button, contacts, alerts)
- [ ] Grafana dashboard and SLO alerting ("99% of alerts processed in under 5 seconds")
- [ ] Terraform and GitOps (Argo CD)
- [ ] Live location link, unsafe-area reports

Optional: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` and the notifier also pings one Telegram chat.
