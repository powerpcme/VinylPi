import os
import sys
import pyaudio
import asyncio
from shazamio import Shazam
import io
import wave
import time
import pylast
import json
import collections
import shutil
import logging

CHUNK = 4096
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100

RECORD_SECONDS = 3
CHECK_INTERVAL = 3
AGGRESSIVE_CHECK_INTERVAL = 2
AGGRESSIVE_CHECK_COUNT = 3
USER_INFO_FILE = "lastfm_user_info.json"
CONSISTENCY_CHECKS = 3
CONSISTENCY_THRESHOLD = 2
CONFIDENCE_THRESHOLD = 0

def store_user_info():
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

        with open(USER_INFO_FILE, "w") as f:
            json.dump(user_info, f)
        return user_info
    else:
        return load_user_info()

def load_user_info():
    if os.path.exists(USER_INFO_FILE):
        with open(USER_INFO_FILE, "r") as f:
            return json.load(f)
    else:
        return store_user_info()

def list_audio_devices():
    p = pyaudio.PyAudio()
    device_count = p.get_device_count()
    for i in range(device_count):
        device_info = p.get_device_info_by_index(i)
        if device_info["maxInputChannels"] > 0:
            device_name = device_info["name"]
            print(f"Device Index {i}: {device_name}")
    p.terminate()

def get_usb_audio_device():
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
    user_info = load_user_info()
    return pylast.LastFMNetwork(
        api_key=user_info["api_key"],
        api_secret=user_info["api_secret"],
        username=user_info["username"],
        password_hash=user_info["password_hash"]
    )

def log_song_to_lastfm(network, artist, title, last_logged_song, logger):
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

async def recognize_song(audio_data, verbose, logger):
    shazam = Shazam()
    with io.BytesIO() as wav_file:
        with wave.open(wav_file, 'wb') as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(pyaudio.get_sample_size(FORMAT))
            wav.setframerate(RATE)
            wav.writeframes(audio_data)
        wav_data = wav_file.getvalue()
    
    try:
        result = await shazam.recognize(wav_data)
        if result and 'track' in result:
            confidence = result.get('confidence', 0)
            if verbose:
                logger.debug(f"Detected: {result['track']['subtitle']} - {result['track']['title']} (Confidence: {confidence})")
            return result['track']['subtitle'], result['track']['title'], confidence
    except Exception as e:
        if verbose:
            logger.error(f"Error in song recognition: {e}")
    return None, None, 0

def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

async def check_song_consistency(recognize_song_func, audio_stream, verbose, logger):
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

        await asyncio.sleep(RECORD_SECONDS)

    song_counts = collections.Counter(song_checks)
    most_common = song_counts.most_common(1)

    if most_common and most_common[0][1] >= CONSISTENCY_THRESHOLD:
        if verbose:
            logger.debug(
                f"Consistent song detected: {most_common[0][0][1]} by {most_common[0][0][0]} "
                f"({most_common[0][1]}/{CONSISTENCY_CHECKS} matches)"
            )
        return most_common[0][0]
    
    if verbose:
        logger.debug("No consistent song detected")
    return None, None

async def aggressive_song_check(recognize_song_func, audio_stream, verbose, logger):
    for i in range(AGGRESSIVE_CHECK_COUNT):
        if verbose:
            logger.debug(f"Aggressive check {i+1}/{AGGRESSIVE_CHECK_COUNT}")
        
        frames = []
        for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
            data = audio_stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)

        audio_data = b''.join(frames)
        song = await check_song_consistency(recognize_song_func, audio_stream, verbose, logger)

        if song and song[0] and song[1] and song[0] != "None" and song[1] != "None":
            return song

        await asyncio.sleep(AGGRESSIVE_CHECK_INTERVAL)
    
    return None, None

def display_tui_track_info(artist, title):
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
