"""VinylPi Library: Core functionality for vinyl record scrobbling.

This module provides the core functionality for the VinylPi application, including:
- Audio device management
- Song recognition using Shazam
- Last.fm integration
- File I/O operations
"""

import asyncio
import collections
import io
import json
import os
import shutil
import time
import wave

import pyaudio
import pylast
from shazamio import Shazam

CHUNK = 4096
FORMAT = pyaudio.paFloat32  # Changed to float32 for better quality
CHANNELS = 1
RATE = 48000  # Changed to 48kHz for better compatibility

RECORD_SECONDS = 5  # Increased recording time for better recognition
CHECK_INTERVAL = 3
AGGRESSIVE_CHECK_INTERVAL = 2
AGGRESSIVE_CHECK_COUNT = 3
USER_INFO_FILE = "lastfm_user_info.json"
CONSISTENCY_CHECKS = 3  # Number of checks
CONSISTENCY_THRESHOLD = 2  # Need at least 2 matches
CONFIDENCE_THRESHOLD = 0  # Ignore confidence since Shazam sometimes returns 0 for valid matches
CHECK_DELAY = 1  # Delay between checks

# Audio level detection settings
SILENCE_THRESHOLD = 0.05  # Audio levels below this are considered silence
ACTIVITY_THRESHOLD = 0.1  # Audio levels above this trigger recognition
ACTIVITY_WINDOW = 2  # Number of consecutive chunks above threshold to exit standby
STANDBY_WINDOW = 5  # Number of consecutive chunks below threshold to enter standby

def is_audio_active(audio_data):
    """Check if there is significant audio activity in the data.

    Args:
        audio_data: Raw audio data to analyze

    Returns:
        float: Maximum amplitude of the audio data
    """
    import numpy as np
    audio_array = np.frombuffer(audio_data, dtype=np.float32)
    return np.max(np.abs(audio_array))

def store_user_info():
    """Store Last.fm user credentials in a JSON file.

    Returns:
        dict: User information including API keys and credentials
    """
    if not os.path.exists(USER_INFO_FILE):
        api_key = input("Enter your Last.fm API key: ")
        api_secret = input("Enter your Last.fm API secret: ")
        username = input("Enter your Last.fm username: ")
        password = input("Enter your Last.fm password: ")
        password_hash = pylast.md5(password)

        user_info = {
            "api_key": api_key,
            "api_secret": api_secret,
            "username": username,
            "password_hash": password_hash
        }

        with open(USER_INFO_FILE, "w", encoding='utf-8') as f:
            json.dump(user_info, f)
        return user_info
    return load_user_info()

def load_user_info():
    """Load Last.fm user credentials from JSON file.

    Returns:
        dict: User information including API keys and credentials
    """
    if os.path.exists(USER_INFO_FILE):
        with open(USER_INFO_FILE, "r", encoding='utf-8') as f:
            return json.load(f)
    return store_user_info()

def list_audio_devices():
    """List all available audio input devices.

    Prints information about each audio device that has at least one input channel.
    """

    p = pyaudio.PyAudio()
    device_count = p.get_device_count()
    for i in range(device_count):
        device_info = p.get_device_info_by_index(i)
        if device_info["maxInputChannels"] > 0:
            device_name = device_info["name"]
            print(f"Device Index {i}: {device_name}")
    p.terminate()

def get_usb_audio_device():
    """Find the first available USB audio device.

    Returns:
        int or None: Index of the first USB audio device, or None if not found
    """
    p = pyaudio.PyAudio()
    try:
        # First try to find a device with 'USB' in the name
        for i in range(p.get_device_count()):
            devinfo = p.get_device_info_by_index(i)
            if devinfo['maxInputChannels'] > 0:
                name = devinfo['name'].lower()
                if 'usb' in name:
                    return i
        
        # If no USB device found, return the first device with input channels
        for i in range(p.get_device_count()):
            devinfo = p.get_device_info_by_index(i)
            if devinfo['maxInputChannels'] > 0:
                return i
    finally:
        p.terminate()
    return None
    """Find the first available USB audio device.

    Returns:
        int or None: Index of the first USB audio device, or None if not found
    """

    p = pyaudio.PyAudio()
    usb_device = None
    for i in range(p.get_device_count()):
        device_info = p.get_device_info_by_index(i)
        if "usb" in device_info["name"].lower() and device_info["maxInputChannels"] > 0:
            usb_device = i
            break
    p.terminate()
    return usb_device

def get_lastfm_network():
    """Initialize and return a Last.fm network connection.

    Returns:
        pylast.LastFMNetwork: Authenticated Last.fm network connection
    """
    user_info = load_user_info()
    return pylast.LastFMNetwork(
        api_key=user_info["api_key"],
        api_secret=user_info["api_secret"],
        username=user_info["username"],
        password_hash=user_info["password_hash"]
    )

def log_song_to_lastfm(network, artist, title, last_logged_song, original_stdout):
    """Log a detected song to Last.fm.

    Args:
        network: Last.fm network connection
        artist: Name of the artist
        title: Title of the song
        last_logged_song: Previously logged song to avoid duplicates
        original_stdout: Original stdout for printing

    Returns:
        tuple: Artist and title of the logged song
    """
    if artist and title and artist != "None" and title != "None":
        if (artist, title) == last_logged_song:
            print(f"Duplicate song detected. Not logging: {title} by {artist}", file=original_stdout, flush=True)
            return last_logged_song
        
        try:
            network.scrobble(artist=artist, title=title, timestamp=int(time.time()))
            print(f"Logged to Last.fm: {title} by {artist}", file=original_stdout, flush=True)
            return (artist, title)
        except Exception as e:
            print(f"Error logging to Last.fm: {e}", file=original_stdout, flush=True)
    else:
        print("Song not logged: Invalid artist or title", file=original_stdout, flush=True)
    return last_logged_song

async def recognize_song(audio_data, verbose, original_stdout):
    # Debug: Check audio data
    import numpy as np
    audio_array = np.frombuffer(audio_data, dtype=np.float32)
    max_amplitude = np.max(np.abs(audio_array))
    if verbose:
        print(f"Audio max amplitude: {max_amplitude}", file=original_stdout, flush=True)
    """Recognize a song from audio data using Shazam.

    Args:
        audio_data: Raw audio data to analyze
        verbose: Whether to print verbose output
        original_stdout: Original stdout for printing

    Returns:
        tuple or None: (artist, title) if song is recognized, None otherwise
    """
    shazam = Shazam()
    # Convert audio to the format Shazam expects
    with io.BytesIO() as wav_file:
        with wave.open(wav_file, 'wb') as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(2)  # Always use 16-bit for Shazam
            wav.setframerate(RATE)
            # Convert float32 to int16
            import numpy as np
            audio_array = np.frombuffer(audio_data, dtype=np.float32)
            audio_array = (audio_array * 32767).astype(np.int16)
            wav.writeframes(audio_array.tobytes())
        wav_data = wav_file.getvalue()
    
    try:
        result = await shazam.recognize(wav_data)
        if result and 'track' in result:
            confidence = result.get('confidence', 0)
            if verbose:
                print(f"Detected: {result['track']['subtitle']} - {result['track']['title']} (Confidence: {confidence})",
                      file=original_stdout, flush=True)
            return result['track']['subtitle'], result['track']['title'], confidence
    except Exception as e:
        if verbose:
            print(f"Error in song recognition: {e}", file=original_stdout, flush=True)
    return None, None, 0

def clear_console():
    """Clear the console screen using the appropriate system command."""

    os.system('cls' if os.name == 'nt' else 'clear')

async def check_song_consistency(recognize_song_func, audio_stream, verbose, original_stdout):
    """Check if a song is consistently recognized across multiple samples.

    Args:
        recognize_song_func: Function to recognize songs
        audio_stream: Audio stream to read from
        verbose: Whether to print verbose output
        original_stdout: Original stdout for printing

    Returns:
        tuple: Most consistent (artist, title) pair or (None, None)
    """

    import collections
    song_checks = []
    confidence_scores = []

    for i in range(CONSISTENCY_CHECKS):
        if verbose:
            print(f"Consistency check {i+1}/{CONSISTENCY_CHECKS}", file=original_stdout, flush=True)
        
        # If we have an audio stream, record from it. Otherwise use the function directly.
        if audio_stream is not None:
            frames = []
            for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                data = audio_stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
            audio_data = b''.join(frames)
        else:
            # Use the function directly as it will handle the audio data
            audio_data = None
        
        # Always pass the audio data we have
        artist, title, confidence = await recognize_song_func(audio_data)
        if verbose:
            print(f"Check {i+1} result: {title} by {artist} (confidence: {confidence:.2f})", file=original_stdout, flush=True)
        
        if artist and title and artist != "None" and title != "None":
            song_checks.append((artist, title))
            confidence_scores.append(confidence)
        
        # Wait between checks to get different parts of the song
        if i < CONSISTENCY_CHECKS - 1:  # Don't wait after the last check
            if verbose:
                print(f"Waiting {CHECK_DELAY}s for next check...", file=original_stdout, flush=True)
            await asyncio.sleep(CHECK_DELAY)

        await asyncio.sleep(RECORD_SECONDS)

    song_counts = collections.Counter(song_checks)
    most_common = song_counts.most_common(1)
    if most_common and most_common[0][1] >= CONSISTENCY_THRESHOLD:
        matching_indices = [i for i, song in enumerate(song_checks) if song == most_common[0][0]]
        avg_confidence = sum(confidence_scores[i] for i in matching_indices) / len(matching_indices)
        
        if verbose:
            print(
                f"Consistent song detected: {most_common[0][0][1]} by {most_common[0][0][0]} "
                f"({most_common[0][1]}/{CONSISTENCY_CHECKS} matches, avg confidence: {avg_confidence:.2f})",
                file=original_stdout,
                flush=True
            )
        return most_common[0][0]
    
    if verbose:
        if song_checks:
            print("Inconsistent results detected:", file=original_stdout, flush=True)
            for song, count in song_counts.items():
                print(f"  {song[1]} by {song[0]}: {count} matches", file=original_stdout, flush=True)
        else:
            print("No songs detected", file=original_stdout, flush=True)
    return None, None

async def aggressive_song_check(recognize_song_func, audio_stream, verbose, original_stdout):
    """Aggressively try to identify a song with quick successive checks.

    Args:
        recognize_song_func: Function to recognize songs
        audio_stream: Audio stream to read from
        verbose: Whether to print verbose output
        original_stdout: Original stdout for printing

    Returns:
        tuple: (artist, title) if song is recognized, (None, None) otherwise
    """

    import collections
    for i in range(AGGRESSIVE_CHECK_COUNT):
        if verbose:
            print(f"Aggressive check {i+1}/{AGGRESSIVE_CHECK_COUNT}", file=original_stdout, flush=True)
        
        # If we have an audio stream, record from it. Otherwise use the function directly.
        if audio_stream is not None:
            frames = []
            for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                data = audio_stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
            audio_data = b''.join(frames)
            song = await check_song_consistency(recognize_song_func, audio_stream, verbose, original_stdout)
        else:
            # Use the function directly as it will handle the audio data
            song = await check_song_consistency(recognize_song_func, None, verbose, original_stdout)
        if song and song[0] and song[1] and song[0] != "None" and song[1] != "None":
            return song
        await asyncio.sleep(AGGRESSIVE_CHECK_INTERVAL)
    
    return None, None

def display_tui_track_info(artist, title):
    """Display track information in a TUI-like format.

    Args:
        artist: Name of the artist
        title: Title of the song
    """

    columns, _ = shutil.get_terminal_size()
    box_width = max(len(artist), len(title)) + 14
    if box_width > columns:
        box_width = min(box_width, columns - 2)

    top_bottom = "+" + "-" * (box_width - 2) + "+"
    title_cut = max(box_width - 14, 1)
    artist_cut = max(box_width - 13, 1)

    title_line = f"| Now Playing: {title:<{title_cut}} |"
    artist_line = f"| Artist:      {artist:<{artist_cut}} |"

    box_lines = [top_bottom, title_line, artist_line, top_bottom]

    left_pad = max(0, (columns - box_width) // 2)
    centered_output = []
    for line in box_lines:
        centered_output.append(" " * left_pad + line)

    return "\n".join(centered_output)