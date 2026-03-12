#!/bin/bash
# Setup autostart for RadioAgent WOZ demo on Raspberry Pi.
#
# Usage:
#   ./scripts/setup_autostart.sh                      # default: demo_woz_dailynews.py
#   ./scripts/setup_autostart.sh demo_woz_talkshow.py # switch to a different demo
#
# To disable autostart:
#   sudo systemctl disable radioagent-demo
#   sudo systemctl stop radioagent-demo

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_FILE="/etc/systemd/system/radioagent-demo.service"
DEMO_SCRIPT="${1:-demo_woz_multiagent.py}"
USER="$(whoami)"

echo "Setting up autostart for: $DEMO_SCRIPT"
echo "Project directory: $PROJECT_DIR"
echo "Running as user: $USER"

# Generate the service file with correct paths
cat > /tmp/radioagent-demo.service << EOF
[Unit]
Description=RadioAgent WOZ Demo
After=network.target sound.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
ExecStart=/usr/bin/python3 $DEMO_SCRIPT
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo cp /tmp/radioagent-demo.service "$SERVICE_FILE"
sudo systemctl daemon-reload
sudo systemctl enable radioagent-demo
sudo systemctl restart radioagent-demo

echo ""
echo "Done! RadioAgent demo will now auto-start on boot."
echo "  Status:  sudo systemctl status radioagent-demo"
echo "  Logs:    sudo journalctl -u radioagent-demo -f"
echo "  Stop:    sudo systemctl stop radioagent-demo"
echo "  Switch:  ./scripts/setup_autostart.sh demo_woz_other.py"
