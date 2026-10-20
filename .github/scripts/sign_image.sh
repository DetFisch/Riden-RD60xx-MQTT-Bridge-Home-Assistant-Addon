#!/usr/bin/env bash
set -euo pipefail
# Fulcio/Rekor may time out even after a successful registry push.
for attempt in 1 2 3 4 5; do
  if cosign sign --yes "$1"; then
    exit 0
  fi
  if [[ "$attempt" -lt 5 ]]; then
    echo "Signing attempt $attempt failed; retrying..." >&2
    sleep "$((2 ** attempt))"
  fi
done
echo "Image signing failed after five attempts: $1" >&2
exit 1
