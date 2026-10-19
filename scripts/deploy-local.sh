#!/usr/bin/env bash
# Build both images, load them into the local kind cluster and (re)deploy everything.
set -euo pipefail
CLUSTER="${CLUSTER:-sos}"
cd "$(dirname "$0")/.."

kubectl config use-context "kind-${CLUSTER}" >/dev/null || {
  echo "Cluster 'kind-${CLUSTER}' not found. Create it with:  kind create cluster --name ${CLUSTER}"; exit 1; }

echo "==> Building images"
docker build -q -f services/sos-api/Dockerfile -t sos-api:latest .
docker build -q -f services/notifier/Dockerfile -t notifier:latest .
docker build -q -f services/web/Dockerfile -t web:latest .

echo "==> Loading images into the cluster"
kind load docker-image sos-api:latest notifier:latest web:latest --name "${CLUSTER}"

echo "==> Applying Kubernetes files"
kubectl apply -f k8s/namespace.yaml
if ! kubectl -n sos get secret sos-secrets >/dev/null 2>&1; then
  echo "==> Creating secrets (random values, kept only inside the cluster, never in git)"
  kubectl -n sos create secret generic sos-secrets \
    --from-literal=postgres-password="$(openssl rand -hex 16)" \
    --from-literal=jwt-secret="$(openssl rand -hex 32)"
fi

echo "==> Loading monitoring settings (Prometheus rules, Grafana dashboard)"
kubectl -n sos create configmap prometheus-config \
  --from-file=prometheus.yml=monitoring/prometheus-k8s.yml --from-file=slo-rules.yml=monitoring/slo-rules.yml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n sos create configmap grafana-datasource --from-file=datasource.yml=monitoring/grafana/datasource.yml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n sos create configmap grafana-dashboard-provider --from-file=dashboards.yml=monitoring/grafana/dashboards.yml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n sos create configmap grafana-dashboard --from-file=sos-dashboard.json=monitoring/grafana/sos-dashboard.json \
  --dry-run=client -o yaml | kubectl apply -f -
if ! kubectl -n sos get secret grafana-admin >/dev/null 2>&1; then
  kubectl -n sos create secret generic grafana-admin --from-literal=password="$(openssl rand -hex 12)"
fi

kubectl apply -f k8s/

echo "==> Waiting for the database"
kubectl -n sos rollout status statefulset/postgres --timeout=180s

echo "==> Restarting the apps with the new images"
kubectl -n sos rollout restart deployment/sos-api deployment/notifier deployment/web
kubectl -n sos rollout status deployment/sos-api --timeout=180s
kubectl -n sos rollout status deployment/notifier --timeout=180s
kubectl -n sos rollout status deployment/web --timeout=180s
# Monitoring pods only need a restart when their settings changed
kubectl -n sos rollout restart deployment/prometheus deployment/grafana >/dev/null
kubectl -n sos rollout status deployment/prometheus --timeout=180s
kubectl -n sos rollout status deployment/grafana --timeout=240s

echo
kubectl -n sos get pods
echo
echo "Done. Open the app with:  bash scripts/open-app.sh"
echo "Dashboard:                   bash scripts/open-grafana.sh"
echo "Or run the scripted demo with:  bash scripts/demo.sh"
