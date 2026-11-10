#!/bin/bash
# The one command: ./start.sh opens the dashboard. Its first page takes the API key, deploys the agent and checks the deployment.
# Nothing is installed: all it needs is python3, 3.9 or newer.
#   DESK_PORT=8400 ./start.sh     if port 8321 is taken
cd "$(dirname "$0")"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  echo "This needs python3, version 3.9 or newer, and nothing else. Found here: $(command -v python3 > /dev/null && python3 --version 2>&1 | head -1 || echo "no python3")."
  echo "On a Mac: run  xcode-select --install  once (Apple's command line tools bring python3), or install Python from python.org. Then run this again."
  exit 1
fi
# The key comes from the dashboard's first page, and from nowhere else: one left in this shell (by Claude Code, say) is somebody else's.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
exec python3 -m harness serve --open ${DESK_PORT:+--port $DESK_PORT}
