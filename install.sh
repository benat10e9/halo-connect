#!/bin/bash
# Halo Connect installer for macOS
# Installs Python dependencies and sets up the agent

set -e

echo ""
echo "  Installing Halo Connect..."
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "  Python 3 not found. Install from python.org"
    exit 1
fi

PYTHON_VER=$(python3 -c "import sys; print(sys.version_info.minor)")
if [ "$PYTHON_VER" -lt "10" ]; then
    echo "  Python 3.10+ required. Current: 3.$PYTHON_VER"
    exit 1
fi

# Install dependencies
echo "  Installing dependencies..."
pip3 install paho-mqtt bleak rumps requests --quiet

echo ""
echo "  ✓ Dependencies installed"
echo ""
echo "  Run setup:"
echo "    python3 setup.py"
echo ""
