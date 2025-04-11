"""VinylPi: A Last.fm scrobbler for vinyl records.

This module provides the main entry point and CLI interface for the VinylPi application,
which automatically detects and scrobbles vinyl records to Last.fm using audio recognition.
"""

import argparse
import asyncio
import logging
import os
import shutil
import sys
from typing import Tuple, Optional

# Redirect stderr to /dev/null if not in verbose mode
class ErrorFilter:
    def __init__(self, verbose):
        self.verbose = verbose
        self.original_stderr = sys.stderr
        self.devnull = open(os.devnull, 'w')
    
    def __enter__(self):
        if not self.verbose:
            sys.stderr = self.devnull
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self.verbose:
            sys.stderr = self.original_stderr
            self.devnull.close()

import pyaudio

from vinylpi_lib import (
    CHUNK, FORMAT, RATE, CHECK_INTERVAL,
    SILENCE_THRESHOLD, SILENCE_CHECK_DURATION, SILENCE_CHECK_INTERVAL,
    get_usb_audio_device, get_lastfm_network, clear_console,
    aggressive_song_check, check_song_consistency, recognize_song,
    log_song_to_lastfm, list_audio_devices, get_device_channels,
    get_audio_level
)

def create_parser() -> argparse.ArgumentParser:
    """Create and configure the command-line argument parser.

    Returns:
        argparse.ArgumentParser: Configured argument parser for VinylPi
    """
    parser = argparse.ArgumentParser(
        description="VinylPi: Last.fm scrobbler for vinyl records",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python vinylpi.py                   # Run with default settings
  python vinylpi.py -v                # Run in verbose mode
  python vinylpi.py -d 2              # Use audio device with index 2
  python vinylpi.py -l                # List available audio devices
  python vinylpi.py -t                # Display a TUI-like output for track info
  python vinylpi.py -v -t             # Combine verbose + TUI
        """
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("-d", "--device", type=int, default=None, help="Select audio device index")
    parser.add_argument("-l", "--list-devices", action="store_true", help="List available audio devices and exit")
    parser.add_argument("-t", "--tui", action="store_true", help="Show track information in a TUI-like interface")
    return parser

args = create_parser().parse_args()

# Configure logging
logging.basicConfig(level=logging.DEBUG if args.verbose else logging.ERROR,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize error filtering
error_filter = ErrorFilter(args.verbose)

def display_tui(current_song: Optional[Tuple[str, str]] = None, is_listening: bool = True) -> None:
    """Display the Terminal User Interface (TUI) with current playback information.

    Args:
        current_song: Tuple of (artist, title) for the currently playing song
        is_listening: Whether the application is currently listening for new tracks
    """
    clear_console()
    terminal_width, _ = shutil.get_terminal_size()

    print("+" + "-" * (terminal_width - 2) + "+")
    print("|" + " VinylPi ".center(terminal_width - 2) + "|")
    print("+" + "-" * (terminal_width - 2) + "+")

    if current_song:
        artist, title = current_song
        print("| Now Playing:".ljust(terminal_width - 1) + "|")
        title_line = "|   Title: %s" % title
        artist_line = "|   Artist: %s" % artist
        print(title_line.ljust(terminal_width - 1) + "|")
        print(artist_line.ljust(terminal_width - 1) + "|")
    else:
        print("|".ljust(terminal_width - 1) + "|")
        print("| No track currently playing".ljust(terminal_width - 1) + "|")
        print("|".ljust(terminal_width - 1) + "|")

    print("+" + "-" * (terminal_width - 2) + "+")
    if is_listening:
        if current_song:
            print("| Checking for new tracks...".ljust(terminal_width - 1) + "|")
        else:
            print("| Listening for tracks...".ljust(terminal_width - 1) + "|")
    else:
        print("| Standby mode - No audio detected".ljust(terminal_width - 1) + "|")
    print("+" + "-" * (terminal_width - 2) + "+")

async def process_song_detection(song, current_song, last_logged_song, no_song_detected_count,
                          lastfm_network, recognize_func, stream, args, logger):
    """Process detected song and handle logging to Last.fm.

    Returns:
        tuple: (current_song, last_logged_song, no_song_detected_count)
    """
    if song and song[0] and song[1]:
        artist, title = song
        # Always log if we have a valid song
        if not args.tui:
            logger.info("Now playing: %s by %s", title, artist)
        last_logged_song = log_song_to_lastfm(
            lastfm_network, artist, title,
            last_logged_song, logger
        )
        return song, last_logged_song, 0
    return None, last_logged_song, no_song_detected_count + 1

async def handle_no_song_detected(no_song_detected_count, recognize_func, stream,
                               lastfm_network, current_song, last_logged_song, args, logger):
    """Handle case when no song is detected for multiple checks.

    Returns:
        tuple: (current_song, last_logged_song, no_song_detected_count)
    """
    if no_song_detected_count >= 3:
        logger.info("No song detected. Trying aggressive detection...")
        song = await aggressive_song_check(recognize_func, stream, args.verbose, logger)
        if song and song[0] and song[1] and song[0] != "None" and song[1] != "None":
            artist, title = song
            if args.tui:
                display_tui(song)
            else:
                logger.info("Detected after aggressive check: %s by %s", title, artist)
            last_logged_song = log_song_to_lastfm(
                lastfm_network, artist, title,
                last_logged_song, logger
            )
            return song, last_logged_song, 0
    if args.tui:
        display_tui()
    else:
        logger.info("No valid song detected")
    return None, last_logged_song, no_song_detected_count

async def main() -> None:
    """Main application entry point.
    
    Handles audio device setup, song recognition, and Last.fm scrobbling.
    Runs continuously until interrupted.
    """
    # Use error filter context
    with error_filter:
        if args.list_devices:
            list_audio_devices()
            return

        selected_device = args.device if args.device is not None else get_usb_audio_device()
        if selected_device is None:
            logger.error("No USB audio device found. Exiting.")
            return

        # Always use mono for better compatibility
        channels = 1
        logger.info("Using audio device index: %s in mono mode", selected_device)

        lastfm_network = get_lastfm_network()

        p = pyaudio.PyAudio()
        stream = p.open(
            format=FORMAT,
            channels=channels,
            rate=RATE,
            input=True,
            input_device_index=selected_device,
            frames_per_buffer=CHUNK
        )

        current_song = None
        last_logged_song = None
        consecutive_same_song_count = 0
        no_song_detected_count = 0

        # Initialize TUI if enabled
        if args.tui:
            display_tui(None)

        async def recognize_func(audio_data):
            return await recognize_song(audio_data, args.verbose, logger)

        try:
            while True:
                try:
                    # Check audio level
                    audio_level = get_audio_level(stream, SILENCE_CHECK_DURATION)
                    
                    if audio_level < SILENCE_THRESHOLD:
                        if args.verbose:
                            logger.debug(f"Audio level {audio_level:.0f} below threshold {SILENCE_THRESHOLD}, entering standby")
                        if args.tui:
                            display_tui(current_song, is_listening=False)
                        # Wait in standby mode, checking audio level periodically
                        while True:
                            await asyncio.sleep(SILENCE_CHECK_INTERVAL)
                            audio_level = get_audio_level(stream, SILENCE_CHECK_DURATION)
                            if audio_level >= SILENCE_THRESHOLD:
                                if args.verbose:
                                    logger.debug(f"Audio level {audio_level:.0f} above threshold, resuming")
                                break
                    
                    # Normal song detection flow
                    song = await check_song_consistency(recognize_func, stream, args.verbose, logger)
                    current_song, last_logged_song, no_song_detected_count = await process_song_detection(
                        song, current_song, last_logged_song, no_song_detected_count,
                        lastfm_network, recognize_func, stream, args, logger
                    )

                    if no_song_detected_count > 0:
                        current_song, last_logged_song, no_song_detected_count = await handle_no_song_detected(
                            no_song_detected_count, recognize_func, stream,
                            lastfm_network, current_song, last_logged_song, args, logger
                        )

                    # Update TUI on every iteration if enabled
                    if args.tui:
                        display_tui(current_song)
                    # Log new songs in non-TUI mode
                    elif song and song != current_song:
                        logger.info("Now playing: %s by %s", song[1], song[0])
                    elif not current_song:
                        logger.info("No track currently playing...")

                    await asyncio.sleep(CHECK_INTERVAL)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("An error occurred: %s", e, exc_info=True)

        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

if __name__ == "__main__":
    asyncio.run(main())
