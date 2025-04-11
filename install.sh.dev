#!/bin/bash

# Exit on any error
set -e

echo "Installing VinylPi..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

# Get the actual user who ran sudo
ACTUAL_USER=$SUDO_USER
if [ -z "$ACTUAL_USER" ]; then
    echo "Could not determine the actual user"
    exit 1
fi

# Get user's home directory
USER_HOME=$(getent passwd $ACTUAL_USER | cut -d: -f6)

# Install system dependencies
echo "Installing system dependencies..."
apt-get update
apt-get install -y python3-pip python3-pyaudio portaudio19-dev

# Install Python packages globally
echo "Installing Python packages..."
pip3 install --break-system-packages -r requirements.txt

# Create log files and set permissions
echo "Setting up log files..."
touch /var/log/vinylpi.log
touch /var/log/vinylpi.error.log
chown $ACTUAL_USER:$ACTUAL_USER /var/log/vinylpi.log
chown $ACTUAL_USER:$ACTUAL_USER /var/log/vinylpi.error.log

# Update service file with correct user and paths
echo "Configuring service..."
INSTALL_DIR="$PWD"
sed -i "s|ExecStart=.*|ExecStart=/usr/bin/python3 $INSTALL_DIR/vinylpi-web/backend/main.py|" vinylpi.service
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$INSTALL_DIR|" vinylpi.service
sed -i "s|User=.*|User=$ACTUAL_USER|" vinylpi.service

# Install and enable service
echo "Installing systemd service..."
cp vinylpi.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable vinylpi
systemctl start vinylpi

echo "VinylPi installation complete!"
echo "The web interface should be available at http://localhost:8000"
echo "To view logs:"
echo "  - Application log: tail -f /var/log/vinylpi.log"
echo "  - Error log: tail -f /var/log/vinylpi.error.log"
