#!/usr/bin/env bash
# Cloudera AI (CAI) Application entrypoint.
#
# CAI sets CDSW_APP_PORT (legacy env name, still used) and expects the app to
# bind 127.0.0.1:$CDSW_APP_PORT. Single worker is a hard invariant: the event
# bus, run engine, and store are in-process singletons — multiple workers would
# break SSE fan-out and duplicate runs. Configure via AGENTICQA_* env vars in the
# CAI Application form (Set Environment Variables). See docs/deploy-cai.md.
set -euo pipefail

cd "$(dirname "$0")/.."

pip install -e . >/dev/null 2>&1 || pip install -r requirements.txt

exec python -m agenticqa.cli serve --host 127.0.0.1 --port "${CDSW_APP_PORT:-8000}"
