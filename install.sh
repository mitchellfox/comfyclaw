#!/bin/bash
set -euo pipefail

# ComfyClaw Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/mitchellfox/comfyclaw/main/install.sh | bash

INSTALL_DIR="${COMFYCLAW_DIR:-$HOME/.comfyclaw}"
BIN_DIR="${COMFYCLAW_BIN:-$HOME/.local/bin}"

echo "Installing ComfyClaw..."

# Clone or update
if [ -d "$INSTALL_DIR" ]; then
    echo "Updating existing installation..."
    cd "$INSTALL_DIR" && git pull --quiet
else
    echo "Cloning ComfyClaw..."
    git clone --quiet https://github.com/mitchellfox/comfyclaw.git "$INSTALL_DIR"
fi

# Create bin directory if needed
mkdir -p "$BIN_DIR"

# Create launcher script (not just a symlink — handles paths properly)
cat > "$BIN_DIR/comfyclaw" << EOF
#!/bin/bash
exec python3 "$INSTALL_DIR/scripts/comfyclaw.py" "\$@"
EOF
chmod +x "$BIN_DIR/comfyclaw"

# Check if BIN_DIR is in PATH
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    echo ""
    echo "⚠️  $BIN_DIR is not in your PATH. Add it:"
    echo ""
    if [ -f "$HOME/.zshrc" ]; then
        echo "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc && source ~/.zshrc"
    else
        echo "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
    fi
    echo ""
fi

echo "✅ ComfyClaw installed! Run 'comfyclaw --help' to get started."
