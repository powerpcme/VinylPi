"""VinylPi Manager

Manages the VinylPi process and provides an interface for the web API.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, List, Callable
import os
import sys

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from vinylpi_lib import (
    CHUNK, FORMAT, RATE, CHECK_INTERVAL,
    get_device_channels, get_lastfm_network, recognize_song,
    check_song_consistency, get_audio_level
)
import pyaudio

class VinylPiManager:
    def __init__(self):
        # Set up logging first
        self.logger = logging.getLogger('vinylpi')
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG)

        # Initialize state
        self.current_device: Optional[int] = None
        self.running: bool = False
        self.current_track: Optional[dict] = None
        self.track_listeners: List[Callable] = []
        self.status_listeners: List[Callable] = []
        self._task: Optional[asyncio.Task] = None
        self.debug_info = {
            'audio_level': 0,
            'last_detection_time': None,
            'detection_count': 0,
            'last_error': None
        }
        self.stream = None
        self.pyaudio = None
        self.lastfm_network = None
        self.no_song_detected_count = 0
        self.last_logged_song = None
        
        # Try to initialize Last.fm if configured
        try:
            self.lastfm_network = get_lastfm_network()
        except Exception as e:
            self.logger.error(f"Error initializing Last.fm: {e}")
        
    def add_track_listener(self, listener: Callable):
        """Add a listener for track updates."""
        self.track_listeners.append(listener)
        
    def add_status_listener(self, listener: Callable):
        """Add a listener for status updates."""
        self.status_listeners.append(listener)
        
    async def _notify_track_listeners(self):
        """Notify all track listeners of the current track."""
        for listener in self.track_listeners:
            try:
                await listener(self.current_track)
            except:
                pass
                
    async def _notify_status_listeners(self):
        """Notify all status listeners of the current status."""
        status = {
            'running': self.running,
            'current_track': self.current_track,
            'device_index': self.current_device
        }
        for listener in self.status_listeners:
            try:
                await listener(status)
            except:
                pass
                
    async def _process_song_detection(self, song):
        """Process a detected song."""
        if song:
            if song != (self.current_track['artist'] if self.current_track else None,
                       self.current_track['title'] if self.current_track else None):
                # Create basic track info
                self.current_track = {
                    'artist': song[0],
                    'title': song[1],
                    'confidence': 0.9,
                    'timestamp': datetime.now().isoformat()
                }

                # Try to get additional info from Last.fm if configured
                if self.lastfm_network:
                    try:
                        # Get track info using Last.fm API
                        track = self.lastfm_network.get_track(song[0], song[1])
                        track_info = track.get_info()
                        self.logger.info(f"Raw track info: {track_info}")
                        
                        # Extract metadata
                        album_name = None
                        album_year = None
                        track_duration = None
                        track_tags = []
                        track_listeners = None
                        track_playcount = None
                        
                        # Get album info
                        if 'album' in track_info:
                            album_name = track_info['album']['title']
                        
                        # Get track duration
                        if 'duration' in track_info:
                            track_duration = int(track_info['duration']) // 1000  # Convert to seconds
                        
                        # Get track tags
                        if 'toptags' in track_info and 'tag' in track_info['toptags']:
                            track_tags = [tag['name'] for tag in track_info['toptags']['tag'][:3]]
                        
                        # Get popularity info
                        if 'listeners' in track_info:
                            track_listeners = track_info['listeners']
                        if 'playcount' in track_info:
                            track_playcount = track_info['playcount']
                        
                        # Try to get release year from wiki or album info
                        if 'wiki' in track_info:
                            try:
                                wiki_text = track_info['wiki']['content']
                                # Look for year patterns in the wiki text
                                import re
                                year_match = re.search(r'\b(19|20)\d{2}\b', wiki_text)
                                if year_match:
                                    album_year = year_match.group(0)
                            except Exception as e:
                                self.logger.error(f"Error parsing wiki: {e}")
                        
                        # Update track info with additional data
                        self.current_track.update({
                            'album': {
                                'name': album_name,
                                'year': album_year
                            } if album_name else None,
                            'duration': track_duration,
                            'tags': track_tags,
                            'listeners': track_listeners,
                            'playcount': track_playcount
                        })
                        
                        # Scrobble to Last.fm
                        try:
                            self.lastfm_network.update_now_playing(artist=song[0], title=song[1])
                            if song != self.last_logged_song:
                                self.lastfm_network.scrobble(artist=song[0], title=song[1],
                                                            timestamp=int(datetime.now().timestamp()))
                                self.last_logged_song = song
                        except Exception as e:
                            self.logger.error(f"Last.fm scrobbling error: {e}")
                    except Exception as e:
                        self.logger.error(f"Error fetching Last.fm track info: {e}")
                else:
                    self.logger.debug("Last.fm not configured, skipping metadata fetch and scrobbling")

                await self._notify_track_listeners()
                        
            self.no_song_detected_count = 0
        else:
            self.no_song_detected_count += 1
            if self.no_song_detected_count > 3:  # Reset after 3 failed detections
                if self.current_track:
                    self.current_track = None
                    await self._notify_track_listeners()
                self.no_song_detected_count = 0
                
    async def _run_vinylpi(self):
        """Main VinylPi loop."""
        try:
            # Initialize PyAudio
            self.pyaudio = pyaudio.PyAudio()
            self.stream = self.pyaudio.open(
                format=FORMAT,
                channels=1,  # Always use mono
                rate=RATE,
                input=True,
                input_device_index=self.current_device,
                frames_per_buffer=CHUNK
            )
            
            async def recognize_func(audio_data):
                return await recognize_song(audio_data, False, self.logger)
            
            while self.running:
                try:
                    # Check audio level
                    audio_level = get_audio_level(self.stream, 0.1)  # 100ms check
                    self.debug_info['audio_level'] = audio_level
                    self.logger.debug(f"Audio level: {audio_level}")
                    await self._notify_status_listeners()
                    
                    if audio_level < 100:  # Silence threshold
                        self.logger.debug("Audio level below threshold, skipping detection")
                        await asyncio.sleep(0.5)  # Check every 500ms
                        continue
                        
                    # Normal song detection flow
                    self.logger.info("Starting song detection...")
                    self.debug_info['last_detection_time'] = datetime.now().isoformat()
                    self.debug_info['detection_count'] += 1
                    song = await check_song_consistency(recognize_func, self.stream, False, self.logger)
                    self.logger.info(f"Song detection result: {song}")
                    await self._process_song_detection(song)
                    
                    await asyncio.sleep(CHECK_INTERVAL)
                    
                except Exception as e:
                    self.logger.error(f"Error in VinylPi loop: {e}")
                    await asyncio.sleep(1)
                    
        except Exception as e:
            self.logger.error(f"Fatal error in VinylPi: {e}")
            self.running = False
            await self._notify_status_listeners()
        finally:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
            if self.pyaudio:
                self.pyaudio.terminate()
                
    async def start(self, device_index: int) -> bool:
        """Start VinylPi with the specified device."""
        if self.running:
            self.logger.warning("Cannot start: VinylPi is already running")
            return False
            
        self.logger.info(f"Starting VinylPi with device index {device_index}")
        self.current_device = device_index
        self.running = True
        self._task = asyncio.create_task(self._run_vinylpi())
        await self._notify_status_listeners()
        return True
        
    async def stop(self) -> bool:
        """Stop VinylPi."""
        if not self.running:
            return False
            
        self.running = False
        if self._task:
            await self._task
            self._task = None
        
        self.current_track = None
        await self._notify_track_listeners()
        await self._notify_status_listeners()
        return True
        

