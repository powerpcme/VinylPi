"""VinylPi library module.

Provides core functionality for audio capture, song recognition, and Last.fm integration.
Includes utilities for audio device management, file I/O, and user configuration.
"""

import asyncio
import collections
import io
import json
import os
import time
import wave

import numpy as np
import pyaudio
import pylast
from shazamio import Shazam

# Audio settings
CHUNK = 2048  # Back to original chunk size
FORMAT = pyaudio.paInt16  # Back to int16 for better compatibility
RATE = 44100  # Standard CD quality rate

def get_device_channels(device_index: int) -> int:
    """Get the number of input channels supported by the device.

    Args:
        device_index: Index of the audio device

    Returns:
        int: Number of input channels (defaults to 2 if cannot determine)
    """
    try:
        p = pyaudio.PyAudio()
        device_info = p.get_device_info_by_index(device_index)
        max_channels = int(device_info.get('maxInputChannels', 2))
        if max_channels <= 0:
            max_channels = 2  # Default to stereo
        p.terminate()
        return max_channels
    except Exception:
        return 2  # Default to stereo if cannot determine

# Recognition settings
RECORD_SECONDS = 5  # Shorter recording time for faster detection
CHECK_INTERVAL = 0.5  # Faster checks
AGGRESSIVE_CHECK_INTERVAL = 0.5
AGGRESSIVE_CHECK_COUNT = 3  # Fewer aggressive checks
CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.json')
CONSISTENCY_CHECKS = 2  # Only need 2 checks
CONSISTENCY_THRESHOLD = 2  # Both must match
CONFIDENCE_THRESHOLD = 0.1  # More lenient confidence threshold

# Audio level detection settings
SILENCE_THRESHOLD = 100  # RMS threshold for silence detection
SILENCE_CHECK_DURATION = 0.5  # Duration in seconds to check for silence
SILENCE_CHECK_INTERVAL = 1  # How often to check for silence when in standby

def load_config():
    """Load configuration from JSON file.

    Returns:
        dict: Configuration including Last.fm credentials
    """
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        return {}
    except Exception as e:
        print(f"Error loading config: {e}")
        return {}



def list_audio_devices():
    """List all available audio input devices.

    Prints device index and name for each input device found.
    """
    p = pyaudio.PyAudio()
    device_count = p.get_device_count()
    for i in range(device_count):
        device_info = p.get_device_info_by_index(i)
        if device_info["maxInputChannels"] > 0:
            device_name = device_info["name"]
            print(f"Device Index {i}: {device_name}")
    p.terminate()



def get_lastfm_network():
    """Initialize and return a Last.fm network instance.

    Returns:
        pylast.LastFMNetwork: Authenticated Last.fm network instance or None if not configured
    """
    try:
        config = load_config()
        if not config:
            return None

        # Check if all required Last.fm fields are present
        required_fields = ["api_key", "api_secret", "username", "password"]
        if not all(k in config for k in required_fields):
            return None

        # Check if any fields are empty strings
        if any(not config[k] for k in required_fields):
            return None
        
        return pylast.LastFMNetwork(
            api_key=config["api_key"],
            api_secret=config["api_secret"],
            username=config["username"],
            password_hash=pylast.md5(config["password"])
        )
    except Exception:
        return None

def log_song_to_lastfm(network: pylast.LastFMNetwork, artist: str, title: str,
                   last_logged_song: tuple, logger) -> tuple:
    """Log a song to Last.fm if it's not a duplicate.

    Args:
        network: Last.fm network instance
        artist: Artist name
        title: Song title
        last_logged_song: Tuple of (artist, title) of the last logged song
        logger: Logger instance

    Returns:
        tuple: The logged song as (artist, title) if successful, or last_logged_song if not
    """
    if artist and title and artist != "None" and title != "None":
        if (artist, title) == last_logged_song:
            logger.info(f"Duplicate song detected. Not logging: {title} by {artist}")
            return last_logged_song
        
        try:
            network.scrobble(artist=artist, title=title, timestamp=int(time.time()))
            logger.info(f"Logged to Last.fm: {title} by {artist}")
            return (artist, title)
        except Exception as e:
            logger.error(f"Error logging to Last.fm: {e}")
    else:
        logger.info("Song not logged: Invalid artist or title")
    return last_logged_song

async def recognize_song(audio_data: bytes, verbose: bool, logger) -> tuple:
    """Recognize a song from audio data using Shazam.

    Args:
        audio_data: Raw audio data
        verbose: Whether to log debug information
        logger: Logger instance

    Returns:
        tuple: (artist, title, confidence) of the recognized song, or (None, None, 0) if not recognized
    """
    shazam = Shazam()
    with io.BytesIO() as wav_file:
        wav = wave.open(wav_file, 'wb')
        frame_size = pyaudio.get_sample_size(FORMAT)
        total_bytes = len(audio_data)
        
        if verbose:
            logger.debug(f"Audio data: {total_bytes} bytes, {frame_size} bytes per sample")
            logger.debug(f"Sample rate: {RATE} Hz, Recording duration: {RECORD_SECONDS} seconds")
        
        # Always use mono for Shazam
        wav.setnchannels(1)
        wav.setsampwidth(frame_size)
        wav.setframerate(RATE)
        wav.writeframes(audio_data)
        wav.close()
        wav_data = wav_file.getvalue()
    
    try:
        result = await shazam.recognize(wav_data)
        if verbose:
            logger.debug(f"Raw Shazam response: {result}")
        
        if result and 'matches' in result and result['matches'] and 'track' in result:
            # Use the first match's presence as confidence
            confidence = 1.0
            artist = result['track'].get('subtitle', '')
            title = result['track'].get('title', '')
            
            if not artist or not title:
                if verbose:
                    logger.debug("Missing artist or title in response")
                return None, None, 0
            
            if confidence < CONFIDENCE_THRESHOLD:
                if verbose:
                    logger.debug(f"Confidence {confidence} below threshold {CONFIDENCE_THRESHOLD}")
                return None, None, 0
            
            if verbose:
                logger.debug(f"Detected: {artist} - {title} (Confidence: {confidence})")
            return artist, title, confidence
        else:
            if verbose:
                logger.debug("No track data in Shazam response")
    except Exception as e:
        if verbose:
            logger.error(f"Error in song recognition: {e}")
            logger.debug("Audio data size: %d bytes", len(audio_data))
    return None, None, 0

def get_audio_level(audio_stream: pyaudio.Stream, duration: float) -> float:
    """Calculate the RMS level of audio over a given duration.

    Args:
        audio_stream: PyAudio stream to read from
        duration: Duration in seconds to analyze

    Returns:
        float: RMS level of the audio
    """
    frames = []
    for _ in range(0, int(RATE / CHUNK * duration)):
        data = audio_stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)

    # Convert to int16 array
    audio_data = np.frombuffer(b''.join(frames), dtype=np.int16)
    # Calculate RMS, ensuring we don't have negative values under the sqrt
    squared = np.square(audio_data.astype(np.float64))
    mean_squared = np.mean(squared) if squared.size > 0 else 0
    rms = np.sqrt(mean_squared) if mean_squared > 0 else 0
    return float(rms)



async def check_song_consistency(recognize_song_func, audio_stream: pyaudio.Stream,
                               verbose: bool, logger) -> tuple:
    """Check if a song is consistently recognized across multiple samples.

    Args:
        recognize_song_func: Function to recognize songs
        audio_stream: Audio input stream
        verbose: Whether to log debug information
        logger: Logger instance

    Returns:
        tuple: (artist, title) if a song is consistently recognized, else (None, None)
    """
    song_checks = []

    for i in range(CONSISTENCY_CHECKS):
        if verbose:
            logger.debug(f"Consistency check {i+1}/{CONSISTENCY_CHECKS}")
        
        frames = []
        for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
            data = audio_stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)

        audio_data = b''.join(frames)
        artist, title, confidence = await recognize_song_func(audio_data)
        if artist and title and confidence >= CONFIDENCE_THRESHOLD:
            song_checks.append((artist, title))
            if verbose:
                logger.debug(f"Found match: {title} by {artist}")

        # Don't wait after the last check
        if i < CONSISTENCY_CHECKS - 1:
            await asyncio.sleep(CHECK_INTERVAL)

    song_counts = collections.Counter(song_checks)
    most_common = song_counts.most_common(1)

    if most_common:
        matches = most_common[0][1]
        if matches >= CONSISTENCY_THRESHOLD:
            if verbose:
                logger.debug(
                    f"Consistent song detected: {most_common[0][0][1]} by {most_common[0][0][0]} "
                    f"({matches}/{CONSISTENCY_CHECKS} matches)"
                )
            return most_common[0][0]
        elif verbose:
            logger.debug(f"Song had {matches} matches but needed {CONSISTENCY_THRESHOLD}")
    
    if verbose:
        logger.debug("No consistent song detected")
    return None, None




