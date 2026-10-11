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

echo "==> Loading images into the cluster"
kind load docker-image sos-api:latest notifier:latest --name "${CLUSTER}"

echo "==> Applying Kubernetes files"
kubectl apply -f k8s/namespace.yaml
if ! kubectl -n sos get secret sos-secrets >/dev/null 2>&1; then
  echo "==> Creating secrets (random values, kept only inside the cluster, never in git)"
  kubectl -n sos create secret generic sos-secrets \
    --from-literal=postgres-password="$(openssl rand -hex 16)" \
    --from-literal=jwt-secret="$(openssl rand -hex 32)"
fi
kubectl apply -f k8s/

echo "==> Waiting for the database"
kubectl -n sos rollout status statefulset/postgres --timeout=180s

echo "==> Restarting the apps with the new images"
kubectl -n sos rollout restart deployment/sos-api deployment/notifier
kubectl -n sos rollout status deployment/sos-api --timeout=180s
kubectl -n sos rollout status deployment/notifier --timeout=180s

echo
kubectl -n sos get pods
echo
echo "Done. Try the demo with:  bash scripts/demo.sh"
