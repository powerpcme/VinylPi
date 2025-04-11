# VinylPi

![VinylPi Logo](img/VinylPi-logo.png)

VinylPi is an intelligent vinyl record player companion that automatically detects and scrobbles your vinyl records to Last.fm. It features a beautiful web interface with rich track metadata display.

## Quick Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/VinylPi.git
   cd VinylPi
   ```

2. Run the installation script:
   ```bash
   sudo ./install.sh
   ```

That's it! The web interface will be available at http://localhost:8000

## Features

- 🎵 Automatic vinyl record detection and scrobbling
- 🎨 Beautiful web interface with rich track metadata
- 📊 Displays comprehensive track information:
  - Artist and title
  - Album name
  - Release year
  - Track duration
  - Genre tags
  - Last.fm listener count and play count
- 🚀 One-command installation
- 🔄 Automatic startup via systemd
- 📈 Real-time audio level monitoring

## Configuration

### Last.fm Setup

1. Get your API credentials by creating an account at https://www.last.fm/api/account/create
2. Open the VinylPi web interface at http://localhost:8000
3. Click the settings icon in the top right
4. Enter your Last.fm credentials:
   - API Key
   - API Secret
   - Username
   - Password
5. Click 'Save' and then 'Test Connection' to verify your credentials

## Usage

1. Open http://localhost:8000 in your web browser
2. Configure your Last.fm credentials in the settings (top right)
3. Select your turntable's audio input device
4. Click "Start VinylPi"
5. Play a record!

VinylPi will automatically start at boot. You can manage the service using standard systemd commands:

```bash
# Start the service
sudo systemctl start vinylpi

# Stop the service
sudo systemctl stop vinylpi

# Restart the service
sudo systemctl restart vinylpi

# View service status
sudo systemctl status vinylpi
```

## Logs

You can monitor VinylPi's operation through its log files:

- Application log: `tail -f /var/log/vinylpi.log`
- Error log: `tail -f /var/log/vinylpi.error.log`
- System logs: `sudo journalctl -u vinylpi -f`

## Troubleshooting

1. **No audio detected**
   - Check if your audio input device is correctly selected
   - Verify that your turntable is properly connected
   - Check the audio levels in the web interface

2. **Songs not being recognized**
   - Ensure your audio levels are sufficient (visible in the web interface)
   - Check if your Last.fm credentials are correct
   - Try adjusting your turntable's volume

3. **Web interface not loading**
   - Check service status: `sudo systemctl status vinylpi`
   - View logs: `tail -f /var/log/vinylpi.error.log`
   - Restart service: `sudo systemctl restart vinylpi`

## License

This project is licensed under the MIT License - see the LICENSE file for details.
