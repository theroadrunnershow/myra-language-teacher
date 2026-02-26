#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

echo '{"async": true, "asyncTimeout": 300000}'

echo "=== SessionStart: Setting up Myra Language Teacher environment ==="

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$PROJECT_DIR"

# ── Python virtual environment + dependencies ─────────────────────────────────
echo "-> Setting up Python environment..."
if [ ! -d "venv" ]; then
  python3 -m venv venv
fi

source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt -r requirements-dev.txt
echo "  Python dependencies installed."

# Persist venv activation for the session
echo "export VIRTUAL_ENV=\"$PROJECT_DIR/venv\"" >> "$CLAUDE_ENV_FILE"
echo "export PATH=\"$PROJECT_DIR/venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"

# ── Google Cloud CLI ──────────────────────────────────────────────────────────
echo "-> Installing Google Cloud CLI..."
if command -v gcloud &>/dev/null; then
  echo "  gcloud already installed: $(gcloud --version 2>&1 | head -1)"
elif command -v apt-get &>/dev/null; then
  # Install via apt on Debian/Ubuntu containers
  apt-get install -y -q apt-transport-https ca-certificates gnupg curl 2>/dev/null
  # Import Google Cloud apt key (Ubuntu 24.04+ recommended method)
  curl -fsSL "https://packages.cloud.google.com/apt/doc/apt-key.gpg" \
    | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    > /etc/apt/sources.list.d/google-cloud-sdk.list
  apt-get update -y -q 2>/dev/null
  apt-get install -y -q google-cloud-cli 2>/dev/null
  echo "  gcloud installed: $(gcloud --version 2>&1 | head -1)"
else
  # Fallback: install via official tarball (no root required)
  GCLOUD_INSTALL_DIR="/opt/google-cloud-sdk"
  if [ ! -d "$GCLOUD_INSTALL_DIR" ]; then
    curl -fsSL "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz" \
      | tar -xz -C /opt
    "$GCLOUD_INSTALL_DIR/install.sh" --quiet --path-update false
  fi
  echo "export PATH=\"$GCLOUD_INSTALL_DIR/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
  echo "  gcloud installed: $("$GCLOUD_INSTALL_DIR/bin/gcloud" --version 2>&1 | head -1)"
fi

echo "=== Setup complete! ==="
