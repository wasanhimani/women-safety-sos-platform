#!/usr/bin/env bash
# Chaos test: send 20 SOS alerts, one per second, and CRASH every notifier pod 5 seconds in
# (force-delete with no grace period, so the pods die instantly instead of shutting down politely).
# Then check that the contact still received all 20. Needs the cluster from deploy-local.sh.
set -uo pipefail
COUNT="${COUNT:-20}"
PF=""
if [ -z "${BASE:-}" ]; then
  PORT="${PORT:-18000}"
  BASE="http://localhost:${PORT}"
  kubectl -n sos port-forward svc/sos-api "${PORT}:8000" >/dev/null 2>&1 &
  PF=$!
  trap '[ -n "$PF" ] && kill $PF 2>/dev/null || true' EXIT
fi
for _ in $(seq 1 30); do curl -sf "${BASE}/healthz" >/dev/null 2>&1 && break; sleep 1; done
curl -sf "${BASE}/healthz" >/dev/null 2>&1 || { echo "Cannot reach the app at ${BASE}. Is the cluster running?"; exit 1; }

json() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)"; }
post() { curl -s -X POST "${BASE}$1" -H 'content-type: application/json' ${3:+-H "Authorization: Bearer $3"} -d "$2"; }
get()  { curl -s "${BASE}$1" -H "Authorization: Bearer $2"; }

ID="$(date +%s)"
ALICE="chaos-a-${ID}@example.com"; BOB="chaos-b-${ID}@example.com"; PASS="chaos-password-1"

echo "1) Creating two test users (Alice sends, Bob receives)"
A_TOKEN="$(post /auth/signup "{\"name\":\"Alice\",\"email\":\"${ALICE}\",\"password\":\"${PASS}\"}" | json "d['access_token']")"
B_TOKEN="$(post /auth/signup "{\"name\":\"Bob\",\"email\":\"${BOB}\",\"password\":\"${PASS}\"}" | json "d['access_token']")"
CID="$(post /contacts "{\"email\":\"${BOB}\"}" "$A_TOKEN" | json "d['id']")"
post "/contacts/${CID}/accept" '{}' "$B_TOKEN" >/dev/null
[ -n "$A_TOKEN" ] && [ -n "$B_TOKEN" ] && [ -n "$CID" ] || { echo "Could not create the test users."; exit 1; }

echo "2) Sending ${COUNT} alerts, one per second. After 5 seconds EVERY notifier pod is crashed."
( sleep 5; echo; echo "   >>> crashing all notifier pods now"; kubectl -n sos delete pod -l app=notifier --grace-period=0 --force --wait=false >/dev/null 2>&1 ) &
KILLER=$!
ACCEPTED=0
for i in $(seq 1 "$COUNT"); do
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE}/sos" -H 'content-type: application/json' \
    -H "Authorization: Bearer ${A_TOKEN}" -d "{\"latitude\":28.6692,\"longitude\":77.4538,\"message\":\"chaos alert ${i}\"}")"
  [ "$code" = "202" ] && ACCEPTED=$((ACCEPTED + 1))
  printf '%s ' "$code"
  sleep 1
done
echo
wait "$KILLER" 2>/dev/null

echo "3) Waiting for the replacement notifier pods to deliver everything (up to 2 minutes)"
GOT=0
for _ in $(seq 1 "${TRIES:-40}"); do
  GOT="$(get /inbox "$B_TOKEN" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null)"
  [[ "$GOT" =~ ^[0-9]+$ ]] || GOT=0
  [ "$GOT" -ge "$ACCEPTED" ] && break
  printf '.'
  sleep 3
done
echo

PENDING="$(kubectl -n sos exec deploy/redis -- redis-cli XINFO GROUPS sos_alerts 2>/dev/null | awk '$0=="pending"{getline; print; exit}')"

echo
echo "-------- RESULT --------"
echo "Accepted by the app:      ${ACCEPTED} of ${COUNT}"
echo "Received by the contact:  ${GOT} of ${COUNT}"
[ -n "${PENDING}" ] && echo "Stuck in the queue:       ${PENDING}"
if [ "$ACCEPTED" -eq "$COUNT" ] && [ "$GOT" -eq "$COUNT" ]; then
  echo "PASS: all ${COUNT} alerts reached the contact even though every notifier pod was crashed mid-burst."
else
  echo "FAIL: not every alert arrived. Copy this whole output and send it to your assistant."
  exit 1
fi
