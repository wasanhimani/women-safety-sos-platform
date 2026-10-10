# SOS Safety Platform

A highly available emergency-alert system built to showcase DevOps/SRE practices.

```
client --POST /sos--> sos-api --XADD--> Redis Stream --XREADGROUP--> notifier --> Telegram
                         |                                              |
                         +----------- /metrics -------- Prometheus <----+---> Grafana + Alertmanager
```

**Why a queue?** The API returns `202` as soon as the alert is durably queued. If the
notifier crashes, unacked alerts stay pending and are reclaimed by another replica, so alerts are not lost.

## Run locally
```bash
pip install -r requirements-dev.txt && pytest -q
docker compose up --build
curl -X POST localhost:8000/sos -H 'content-type: application/json' \
  -d '{"user_id":"demo","latitude":28.67,"longitude":77.45}'
docker compose logs notifier        # dry-run message appears without Telegram credentials
```
Prometheus: http://localhost:9090  Grafana: http://localhost:3000 (admin/admin, add Prometheus at http://prometheus:9090)

For real Telegram delivery: create a bot with @BotFather, then
`TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... docker compose up`.

## Run on Kubernetes (kind)
```bash
kind create cluster --name sos
docker build -t sos-api:latest services/sos-api && docker build -t notifier:latest services/notifier
kind load docker-image sos-api:latest notifier:latest --name sos
kubectl apply -f k8s/namespace.yaml && kubectl apply -f k8s/
```

## SLO
99% of SOS alerts delivered within 5 seconds (`monitoring/slo-rules.yml`).

## Roadmap
- [x] Phase 1: API, queue, notifier, Docker Compose, CI + Trivy, K8s manifests
- [ ] Phase 2: Terraform (cloud cluster), push images to GHCR, Argo CD GitOps
- [ ] Phase 3: kube-prometheus-stack, Grafana SLO dashboard, Alertmanager to Slack
- [ ] Phase 4: Sealed Secrets, network policies, rate limiting, Checkov/Cosign/SBOM
- [ ] Phase 5: Chaos test (kill pods during load), record results here
- [ ] Phase 6: Live-location link (WebSockets) and incident heatmap
