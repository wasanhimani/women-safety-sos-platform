# Women Safety SOS Platform

A backend for a personal-safety app. A user presses an SOS button; their trusted contacts see the alert
with a live map link. The design goal is that **an alert is never lost**, even when parts of the system fail.

```
                   login token (no database needed to check it)
 phone/app ──POST /sos──▶ sos-api ──▶ Redis Stream ──▶ notifier ──▶ PostgreSQL ──▶ contacts read /inbox
                              │            (durable queue)    │
                              └──────── /metrics ─────────────┴──▶ Prometheus ──▶ Grafana
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
bash scripts/demo.sh             # two users sign up, one presses SOS, the other sees it
```

## CI
GitHub Actions runs the tests, builds both images and scans them with Trivy; HIGH/CRITICAL findings fail the build.

## Status and roadmap
- [x] Alert pipeline, accounts, contacts, inbox, history
- [x] Docker, Kubernetes manifests (replicas, probes, rolling updates, pod disruption budget), CI with security scan
- [x] Chaos test: all notifier pods deleted during a burst of 20 alerts, none lost
- [ ] Mobile-friendly web app (SOS button, contacts, inbox)
- [ ] Grafana dashboard and SLO alerting ("99% of alerts processed in under 5 seconds")
- [ ] Terraform and GitOps (Argo CD)
- [ ] Live location link, unsafe-area reports

Optional: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` and the notifier also pings one Telegram chat.
