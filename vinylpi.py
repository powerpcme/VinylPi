import argparse
import sys
import os
import asyncio
import pyaudio
import shutil
import logging
from typing import Tuple, Optional

from vinylpi_lib import (
    CHUNK, FORMAT, CHANNELS, RATE, CHECK_INTERVAL,
    get_usb_audio_device, get_lastfm_network, clear_console,
    aggressive_song_check, check_song_consistency, recognize_song,
    log_song_to_lastfm, list_audio_devices
)

def create_parser() -> argparse.ArgumentParser:
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

def display_tui(current_song: Optional[Tuple[str, str]] = None, is_listening: bool = True) -> None:
    clear_console()
    terminal_width, _ = shutil.get_terminal_size()
    
    print("+" + "-" * (terminal_width - 2) + "+")
    print("|" + " VinylPi ".center(terminal_width - 2) + "|")
    print("+" + "-" * (terminal_width - 2) + "+")
    
    if current_song:
        artist, title = current_song
        print(f"| Now Playing:".ljust(terminal_width - 1) + "|")
        print(f"|   Title: {title}".ljust(terminal_width - 1) + "|")
        print(f"|   Artist: {artist}".ljust(terminal_width - 1) + "|")
    else:
        print("|".ljust(terminal_width - 1) + "|")
        print("| No track currently playing".ljust(terminal_width - 1) + "|")
        print("|".ljust(terminal_width - 1) + "|")
    
    print("+" + "-" * (terminal_width - 2) + "+")
    if is_listening:
        print("| Listening for new tracks...".ljust(terminal_width - 1) + "|")
    print("+" + "-" * (terminal_width - 2) + "+")

async def main() -> None:
    if args.list_devices:
        list_audio_devices()
        return

    selected_device = args.device if args.device is not None else get_usb_audio_device()
    if selected_device is None:
        logger.error("No USB audio device found. Exiting.")
        return

    logger.info(f"Using audio device index: {selected_device}")

    lastfm_network = get_lastfm_network()

    p = pyaudio.PyAudio()
    stream = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        input_device_index=selected_device,
        frames_per_buffer=CHUNK
    )

    current_song = None
    last_logged_song = None
    consecutive_same_song_count = 0
    no_song_detected_count = 0

    async def recognize_func(audio_data):
        return await recognize_song(audio_data, args.verbose, logger)

    try:
        while True:
            try:
                if args.tui:
                    display_tui(current_song)
                else:
                    logger.info("Starting song detection...")

                song = await check_song_consistency(recognize_func, stream, args.verbose, logger)

                if song and song[0] and song[1] and song[0] != "None" and song[1] != "None":
                    artist, title = song
                    if song != current_song:
                        consecutive_same_song_count = 1
                        if args.tui:
                            display_tui(song)
                        else:
                            logger.info(f"Now playing: {title} by {artist}")
                        last_logged_song = log_song_to_lastfm(
                            lastfm_network, artist, title,
                            last_logged_song, logger
                        )
                        current_song = song
                        no_song_detected_count = 0
                    else:
                        consecutive_same_song_count += 1
                        logger.debug(f"Still playing: {title} by {artist}")
                else:
                    consecutive_same_song_count = 0
                    no_song_detected_count += 1
                    if no_song_detected_count >= 3:
                        logger.info("No song detected. Trying aggressive detection...")
                        song = await aggressive_song_check(recognize_func, stream, args.verbose, logger)
                        if song and song[0] and song[1] and song[0] != "None" and song[1] != "None":
                            artist, title = song
                            if args.tui:
                                display_tui(song)
                            else:
                                logger.info(f"Detected after aggressive check: {title} by {artist}")
                            last_logged_song = log_song_to_lastfm(
                                lastfm_network, artist, title,
                                last_logged_song, logger
                            )
                            current_song = song
                            no_song_detected_count = 0
                        else:
                            if args.tui:
                                display_tui()
                            else:
                                logger.info("No valid song detected")
                            current_song = None
                
                await asyncio.sleep(CHECK_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"An error occurred: {e}", exc_info=True)

    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

if __name__ == "__main__":
    asyncio.run(main())
