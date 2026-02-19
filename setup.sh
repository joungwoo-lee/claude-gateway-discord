#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_BIN_DIR="${INSTALL_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER_NAME="claudegateway"
LAUNCHER_PATH="$INSTALL_BIN_DIR/$LAUNCHER_NAME"
PATH_LINE="export PATH=\"$INSTALL_BIN_DIR:\$PATH\""

ensure_path_in_profile() {
  local profile="$1"
  [ -f "$profile" ] || touch "$profile"
  if ! grep -Fq "$PATH_LINE" "$profile"; then
    printf "\n# added by claude-gateway-discord setup\n%s\n" "$PATH_LINE" >> "$profile"
    echo "✅ PATH added to $profile"
  else
    echo "✅ PATH already present in $profile"
  fi
}

SKILLS_SRC_DIR="$PROJECT_DIR/.claude/skills"
SKILLS_DST_DIR="$HOME/.claude/skills"

printf "\n[1/8] Checking python...\n"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "❌ $PYTHON_BIN not found. Please install Python 3.10+ first."
  exit 1
fi

printf "\n[2/8] Preparing .env file...\n"
if [ ! -f "$PROJECT_DIR/.env" ] && [ -f "$PROJECT_DIR/.env.example" ]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
  echo "✅ .env created from .env.example"
elif [ -f "$PROJECT_DIR/.env" ]; then
  echo "✅ existing .env found"
else
  echo "⚠️  .env.example not found, skipping .env creation"
fi

printf "\n[3/8] Creating virtual environment (if missing)...\n"
if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  echo "✅ venv created: $VENV_DIR"
else
  echo "✅ existing venv found: $VENV_DIR"
fi

printf "\n[4/8] Installing dependencies...\n"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

printf "\n[5/8] Installing launcher command: $LAUNCHER_NAME\n"
mkdir -p "$INSTALL_BIN_DIR"

cat > "$LAUNCHER_PATH" <<EOF
#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$PROJECT_DIR"
VENV_PY="\$PROJECT_DIR/venv/bin/python"
MAIN_PY="\$PROJECT_DIR/main.py"

if [ ! -x "\$VENV_PY" ]; then
  echo "❌ venv python not found: \$VENV_PY"
  echo "Run: \"\$PROJECT_DIR/setup.sh\" first."
  exit 1
fi

exec "\$VENV_PY" "\$MAIN_PY" "\$@"
EOF

chmod +x "$LAUNCHER_PATH"

printf "\n[6/8] Linking skills to ~/.claude/skills\n"
mkdir -p "$SKILLS_DST_DIR"
for skill_dir in "$SKILLS_SRC_DIR"/*/; do
  skill_name="$(basename "$skill_dir")"
  target="$SKILLS_DST_DIR/$skill_name"
  if [ -L "$target" ]; then
    rm "$target"
  fi
  if [ -e "$target" ]; then
    echo "⚠️  Skipped $skill_name (already exists and is not a symlink)"
  else
    ln -s "$skill_dir" "$target"
    echo "✅ $skill_name → $target"
  fi
done

echo "\n[7/8] Ensuring PATH is persisted automatically"
ensure_path_in_profile "$HOME/.bashrc"
ensure_path_in_profile "$HOME/.zshrc"

echo "\n[8/8] Final checks"
if command -v "$LAUNCHER_NAME" >/dev/null 2>&1; then
  echo "✅ '$LAUNCHER_NAME' is available now"
else
  echo "⚠️ New shell required to apply updated PATH."
  echo "   Run once now: source ~/.bashrc  (or source ~/.zshrc)"
fi

echo "\nDone."
echo "Run from anywhere: $LAUNCHER_NAME"
