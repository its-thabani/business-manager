#!/usr/bin/env bash
# Render build step. Exits on the first failure so a broken migration cannot
# result in a half-deployed application.
set -o errexit
set -o pipefail
set -o nounset

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --no-input

# Migrations are additive by design; no destructive operation is ever generated.
python manage.py migrate --no-input

# Categories and rules are seeded idempotently, so a deploy never duplicates or
# resets them, and any edits made through the UI survive.
python manage.py seed_finance
python manage.py bootstrap_operator
