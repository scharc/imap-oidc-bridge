#!/usr/bin/env bash
# Seed Greenmail with test users via its management API.
# Run after `docker compose up` once the stack is healthy.
set -euo pipefail

API="${GREENMAIL_API:-http://127.0.0.1:38080/api/user}"

users=(
  "alice@example.com:hunter2"
  "bob@example.com:s3cret"
)

for spec in "${users[@]}"; do
  email="${spec%%:*}"
  pw="${spec##*:}"
  echo "creating $email"
  curl --fail --silent --show-error \
    -X POST "$API" \
    -H "Content-Type: application/json" \
    -d "{\"login\":\"$email\",\"email\":\"$email\",\"password\":\"$pw\"}" \
    > /dev/null
done

echo "done"
