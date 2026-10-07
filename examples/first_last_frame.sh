#!/usr/bin/env bash
# First + last frame with plain curl and jq. Frame modes take the first frame's
# shape, so aspect_ratio is "adaptive" and no reference assets may be added.
set -euo pipefail
: "${MINIMAX_H3_API_KEY:?export MINIMAX_H3_API_KEY first}"
BASE="${MINIMAX_H3_BASE_URL:-https://argolink.io/v1}"
AUTH="Authorization: Bearer $MINIMAX_H3_API_KEY"

REQUEST_ID=$(curl -fsS "$BASE/videos/generations" -H "$AUTH" -H "Content-Type: application/json" -d '{
  "model": "minimax-h3",
  "prompt": "The bud slowly opens into a full red peony while morning light sweeps across the petals",
  "duration": 5,
  "resolution": "768p",
  "aspect_ratio": "adaptive",
  "start_image": {"url": "https://example.com/peony-bud.png"},
  "end_image": {"url": "https://example.com/peony-open.png"}
}' | jq -er .request_id)
echo "request_id: $REQUEST_ID" >&2

while :; do
  JOB=$(curl -fsS "$BASE/videos/$REQUEST_ID" -H "$AUTH")
  case $(jq -r .status <<<"$JOB") in
    done) break ;;
    failed|expired) jq '{status, error}' <<<"$JOB" >&2; exit 1 ;;
    *) jq -r '"\(.status) \(.progress // 0)%"' <<<"$JOB" >&2; sleep 10 ;;
  esac
done

curl -fsSL "$BASE/videos/$REQUEST_ID/content" -H "$AUTH" -o peony.mp4
jq .usage <<<"$JOB"
