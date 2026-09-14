#!/bin/sh
# Copy only what the container needs to the production host.
#   ./deploy.sh user@host:/path/to/pyunto-agent
# Whitelist (no venv, no keys, no caches):
set -e
[ -n "$1" ] || { echo "usage: ./deploy.sh user@host:/path/to/pyunto-agent"; exit 1; }
cd "$(dirname "$0")"
rsync -av --delete \
  --include='/Dockerfile' --include='/pyproject.toml' --include='/README.md' \
  --include='/persona.md' --include='/DEPLOY.ja.md' --include='/.dockerignore' \
  --include='/pyunto_agent/' --include='/pyunto_agent/*.py' \
  --exclude='*' \
  ./ "$1/"
