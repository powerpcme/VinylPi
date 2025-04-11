"""VinylPi Web Backend

FastAPI backend for controlling and monitoring VinylPi.
"""

import asyncio
import json
import logging
import os
import sys
from typing import List

import pyaudio
import pylast
from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Import VinylPi modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from vinylpi_lib import get_device_channels
from vinylpi_manager import VinylPiManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

app = FastAPI(title="VinylPi Web")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class AudioDevice(BaseModel):
    index: int
    name: str
    channels: int

class CurrentTrack(BaseModel):
    artist: str
    title: str
    confidence: float
    timestamp: str

class LastFmConfig(BaseModel):
    api_key: str
    api_secret: str
    username: str
    password: str

# Constants
CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'config.json')

# Initialize VinylPi manager
manager = VinylPiManager()
connected_clients: List[WebSocket] = []

def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logging.error(f"Error loading config: {e}")
        return {}

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        return True
    except Exception as e:
        logging.error(f"Error saving config: {e}")
        return False

def test_lastfm_connection(config):
    try:
        network = pylast.LastFMNetwork(
            api_key=config['api_key'],
            api_secret=config['api_secret'],
            username=config['username'],
            password_hash=pylast.md5(config['password'])
        )
        # Try to fetch user info to verify credentials
        user = network.get_user(config['username'])
        user.get_name()
        return True, None
    except Exception as e:
        return False, str(e)

@app.get("/devices", response_model=List[AudioDevice])
async def get_audio_devices():
    """Get list of available audio devices."""
    devices = []
    p = pyaudio.PyAudio()
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:  # Only include input devices
                devices.append(AudioDevice(
                    index=i,
                    name=info['name'],
                    channels=info['maxInputChannels']
                ))
    finally:
        p.terminate()
    return devices

@app.get("/lastfm-config")
async def get_lastfm_config():
    """Get Last.fm configuration."""
    config = load_config()
    return {
        "api_key": config.get("api_key", ""),
        "api_secret": config.get("api_secret", ""),
        "username": config.get("username", ""),
        "password": config.get("password", "")
    }

@app.post("/lastfm-config")
async def save_lastfm_config(config: LastFmConfig):
    """Save Last.fm configuration."""
    current_config = load_config()
    
    # Update config with new values
    current_config.update({
        "api_key": config.api_key,
        "api_secret": config.api_secret,
        "username": config.username,
        "password": config.password
    })
    
    if not save_config(current_config):
        raise HTTPException(status_code=500, detail="Failed to save configuration")
    
    return {"status": "success"}

@app.get("/test-lastfm")
async def test_lastfm():
    """Test Last.fm connection with current configuration."""
    config = load_config()
    if not all(k in config for k in ["api_key", "api_secret", "username", "password"]):
        return {"success": False, "error": "Missing Last.fm configuration"}
    
    success, error = test_lastfm_connection(config)
    return {"success": success, "error": error}

@app.get("/status")
async def get_status():
    """Get current VinylPi status."""
    return {
        "running": manager.running,
        "current_track": manager.current_track,
        "device_index": manager.current_device
    }

class StartRequest(BaseModel):
    device_index: int

@app.post("/start")
async def start_vinylpi(request: StartRequest):
    """Start VinylPi process with specified device."""
    success = await manager.start(request.device_index)
    return {"status": "started" if success else "already_running"}

@app.post("/stop")
async def stop_vinylpi():
    """Stop VinylPi process."""
    success = await manager.stop()
    return {"status": "stopped" if success else "not_running"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await websocket.accept()
    connected_clients.append(websocket)
    
    async def track_listener(track):
        if track:
            try:
                await websocket.send_json({"type": "track_update", "data": track})
            except:
                pass
    
    async def status_listener(status):
        try:
            await websocket.send_json({"type": "status_update", "data": status})
        except:
            pass
    
    # Add listeners
    manager.add_track_listener(track_listener)
    manager.add_status_listener(status_listener)
    
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except:
        connected_clients.remove(websocket)

# Serve frontend static files - mount this last
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
