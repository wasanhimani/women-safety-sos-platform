#!/usr/bin/env bash
# Walks through the whole product once: two people sign up, one names the other as a
# trusted contact, presses SOS, and the contact sees the alert in their inbox.
set -euo pipefail
PORT="${PORT:-18000}"
BASE="http://localhost:${PORT}"

kubectl -n sos port-forward svc/sos-api "${PORT}:8000" >/dev/null 2>&1 &
PF=$!
trap 'kill $PF 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do curl -sf "${BASE}/healthz" >/dev/null 2>&1 && break; sleep 1; done

ID="$(date +%s)"
ALICE="alice${ID}@example.com"; BOB="bob${ID}@example.com"; PASS="demo-password-1"
json() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)"; }
pretty() { python3 -m json.tool; }
post() { curl -s -X POST "${BASE}$1" -H 'content-type: application/json' ${3:+-H "Authorization: Bearer $3"} -d "$2"; }
get()  { curl -s "${BASE}$1" -H "Authorization: Bearer $2"; }

echo "1) Alice and Bob sign up"
A_TOKEN="$(post /auth/signup "{\"name\":\"Alice\",\"email\":\"${ALICE}\",\"password\":\"${PASS}\"}" | json "d['access_token']")"
B_TOKEN="$(post /auth/signup "{\"name\":\"Bob\",\"email\":\"${BOB}\",\"password\":\"${PASS}\"}" | json "d['access_token']")"
echo "   done"

echo "2) Alice names Bob as a trusted contact, and Bob accepts"
CID="$(post /contacts "{\"email\":\"${BOB}\"}" "$A_TOKEN" | json "d['id']")"
post "/contacts/${CID}/accept" '{}' "$B_TOKEN" | pretty

echo "3) Alice presses the SOS button"
post /sos '{"latitude":28.6692,"longitude":77.4538,"message":"I feel unsafe, please call me"}' "$A_TOKEN" | pretty

sleep 3
echo "4) Bob's inbox"
get /inbox "$B_TOKEN" | pretty

echo "5) Alice's sent history"
get /alerts/sent "$A_TOKEN" | pretty
