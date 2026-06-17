#!/bin/bash
# Halo Connect installer for macOS

set -e

echo ""
echo "  Installing Halo Connect..."
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "  Python 3 not found. Install from python.org"
    exit 1
fi

# Create virtual environment inside the project
python3 -m venv .venv
source .venv/bin/activate

echo "  Installing dependencies..."
pip install --quiet paho-mqtt bleak rumps requests

# Create launcher scripts that use the venv
cat > run.sh << 'LAUNCHER'
#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
python3 run.py "$@"
LAUNCHER
chmod +x run.sh

cat > setup.sh << 'LAUNCHER'
#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
python3 setup.py "$@"
LAUNCHER
chmod +x setup.sh

echo ""
echo "  ✓ Done. Now run:"
echo ""
echo "    ./setup.sh"
echo ""
