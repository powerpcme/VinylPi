"""VinylPi: A Last.fm scrobbler for vinyl records with Shazam integration.

This module provides the main CLI interface and program flow for the VinylPi application.
It handles command-line arguments, audio device selection, and the main recording loop.
"""

import argparse
import asyncio
import logging
import os
import shutil
import sys
from typing import Tuple

import pyaudio

from vinylpi_lib import (
    CHUNK, FORMAT, CHANNELS, RATE, CHECK_INTERVAL, RECORD_SECONDS,
    SILENCE_THRESHOLD, ACTIVITY_THRESHOLD, ACTIVITY_WINDOW, STANDBY_WINDOW,
    get_usb_audio_device, get_lastfm_network, clear_console,
    aggressive_song_check, check_song_consistency, recognize_song,
    log_song_to_lastfm, list_audio_devices, is_audio_active
)

def create_parser():
    """Create and configure the argument parser for VinylPi.

    Returns:
        argparse.ArgumentParser: Configured argument parser
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
  python vinylpi.py -v -t             # Combine verbose + TUI""")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("-d", "--device", type=int, default=None, help="Select audio device index")
    parser.add_argument("-l", "--list-devices", action="store_true", help="List available audio devices and exit")
    parser.add_argument("-t", "--tui", action="store_true", help="Show track information in a TUI-like interface")
    return parser

args = create_parser().parse_args()

# Store the original stdout
original_stdout = sys.stdout

# Redirect stderr to /dev/null if not in verbose mode
if not args.verbose:
    sys.stderr = open(os.devnull, 'w')

def display_tui(current_song=None, is_listening=True, standby=False, status_text=None) -> None:
    """Display the current playback status in a TUI format.

    Args:
        current_song: Optional tuple of (artist, title)
        is_listening: Whether the program is currently listening for audio
    """
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
        if status_text:
            print(f"| {status_text}".ljust(terminal_width - 1) + "|")
        elif standby:
            print("| Standby mode - Waiting for audio...".ljust(terminal_width - 1) + "|")
        else:
            print("| Listening for new tracks...".ljust(terminal_width - 1) + "|")
    print("+" + "-" * (terminal_width - 2) + "+")

async def main() -> None:
    """Main program loop that handles audio recording and song recognition.

    This function initializes the audio device, sets up Last.fm connection,
    and continuously monitors audio input for song recognition.
    """
    if args.list_devices:
        list_audio_devices()
        sys.exit(0)

    if args.device is not None:
        selected_device = args.device
        if args.verbose:
            print(f"Using user-specified audio device index: {selected_device}", file=original_stdout)
    else:
        # Try to find a suitable audio device
        p = pyaudio.PyAudio()
        try:
            # First try sysdefault
            for i in range(p.get_device_count()):
                devinfo = p.get_device_info_by_index(i)
                if devinfo['maxInputChannels'] > 0 and 'sysdefault' in devinfo['name'].lower():
                    selected_device = i
                    if args.verbose:
                        print(f"Using sysdefault audio device: {devinfo['name']}", file=original_stdout)
                    break
            else:
                # If sysdefault not found, try USB or any input device
                selected_device = get_usb_audio_device()
                if selected_device is None:
                    print("No suitable audio device found. Available devices:", file=original_stdout)
                    list_audio_devices()
                    sys.exit(1)
        finally:
            p.terminate()
        
        if args.verbose:
            p = pyaudio.PyAudio()
            try:
                devinfo = p.get_device_info_by_index(selected_device)
                print(f"Using audio device: {devinfo['name']} (index: {selected_device})", file=original_stdout)
                print(f"Device info: channels={devinfo['maxInputChannels']}, rate={devinfo['defaultSampleRate']}", file=original_stdout)
            finally:
                p.terminate()

    lastfm_network = get_lastfm_network()

    p = pyaudio.PyAudio()
    
    def create_stream():
        return p.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            input_device_index=selected_device,
            frames_per_buffer=CHUNK
        )
    
    stream = create_stream()

    current_song = None
    last_logged_song = None
    consecutive_same_song_count = 0
    
    # Standby mode variables
    in_standby = False
    silence_count = 0
    activity_count = 0
    no_song_detected_count = 0

    while True:
        try:
            # Record and analyze audio data for activity detection
            frames = []
            max_amplitude = 0
            stream_error = False
            
            for _ in range(int(RATE / CHUNK * RECORD_SECONDS)):
                try:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    frames.append(data)
                    # Check audio level of this chunk
                    chunk_amplitude = is_audio_active(data)
                    max_amplitude = max(max_amplitude, chunk_amplitude)
                except IOError as e:
                    if "Stream closed" in str(e):
                        print("Stream closed, reopening...", file=original_stdout)
                        try:
                            stream.close()
                        except:
                            pass
                        stream = create_stream()
                        stream_error = True
                        break
                    else:
                        print(f"IO Error reading from audio stream: {e}", file=original_stdout)
                except Exception as e:
                    print(f"Error reading from audio stream: {e}", file=original_stdout)
                    continue
            
            # If we had a stream error, skip this iteration
            if stream_error:
                await asyncio.sleep(1)  # Brief pause before retry
                continue

            audio_data = b''.join(frames)

            # Update standby state based on audio levels
            if max_amplitude < SILENCE_THRESHOLD:
                silence_count += 1
                activity_count = 0
                if silence_count >= STANDBY_WINDOW and not in_standby:
                    in_standby = True
                    if args.verbose:
                        print("Entering standby mode - no audio detected", file=original_stdout)
            elif max_amplitude > ACTIVITY_THRESHOLD:
                activity_count += 1
                silence_count = 0
                if activity_count >= ACTIVITY_WINDOW and in_standby:
                    in_standby = False
                    if args.verbose:
                        print("Exiting standby mode - audio activity detected", file=original_stdout)

            # Update display with audio levels
            if args.tui:
                status = "Standby" if in_standby else "Active"
                level = f"Audio: {max_amplitude:.3f}"
                display_tui(current_song, standby=in_standby, status_text=f"{status} - {level}")
            elif args.verbose:
                print(f"Audio level: {max_amplitude:.3f} ({'Standby' if in_standby else 'Active'})", file=original_stdout, flush=True)
                if not in_standby:
                    print("Starting song detection...", file=original_stdout, flush=True)

            # Skip song recognition if in standby mode
            if in_standby:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            async def recognize_func(audio_data):
                return await recognize_song(audio_data, args.verbose, original_stdout)

            # Record and analyze audio data for activity detection
            frames = []
            max_amplitude = 0
            stream_error = False
            
            for _ in range(int(RATE / CHUNK * RECORD_SECONDS)):
                try:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    frames.append(data)
                    # Check audio level of this chunk
                    chunk_amplitude = is_audio_active(data)
                    max_amplitude = max(max_amplitude, chunk_amplitude)
                except IOError as e:
                    if "Stream closed" in str(e):
                        print("Stream closed, reopening...", file=original_stdout)
                        try:
                            stream.close()
                        except:
                            pass
                        stream = create_stream()
                        stream_error = True
                        break
                    else:
                        print(f"IO Error reading from audio stream: {e}", file=original_stdout)
                except Exception as e:
                    print(f"Error reading from audio stream: {e}", file=original_stdout)
                    continue
            
            # If we had a stream error, skip this iteration
            if stream_error:
                await asyncio.sleep(1)  # Brief pause before retry
                continue

            audio_data = b''.join(frames)

            # Update standby state based on audio levels
            if max_amplitude < SILENCE_THRESHOLD:
                silence_count += 1
                activity_count = 0
                if silence_count >= STANDBY_WINDOW and not in_standby:
                    in_standby = True
                    if args.verbose:
                        print("Entering standby mode - no audio detected", file=original_stdout)
            elif max_amplitude > ACTIVITY_THRESHOLD:
                activity_count += 1
                silence_count = 0
                if activity_count >= ACTIVITY_WINDOW and in_standby:
                    in_standby = False
                    if args.verbose:
                        print("Exiting standby mode - audio activity detected", file=original_stdout)

            # Update display with audio levels
            if args.tui:
                status = "Standby" if in_standby else "Active"
                level = f"Audio: {max_amplitude:.3f}"
                display_tui(current_song, standby=in_standby, status_text=f"{status} - {level}")
            elif args.verbose:
                print(f"Audio level: {max_amplitude:.3f} ({'Standby' if in_standby else 'Active'})", file=original_stdout, flush=True)
                if not in_standby:
                    print("Starting song detection...", file=original_stdout, flush=True)

            # Skip song recognition if in standby mode
            if in_standby:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            # Process the recorded audio for song recognition
            async def recognize_with_data(audio_stream):
                # We already have the audio data, just return it
                return await recognize_func(audio_data)
            
            # Pass None as the audio stream since we already have the data
            song = await check_song_consistency(recognize_with_data, None, args.verbose, original_stdout)

            if song and song[0] and song[1] and song[0] != "None" and song[1] != "None":
                artist, title = song
                if song != current_song:
                    consecutive_same_song_count = 1
                    if args.tui:
                        display_tui(song)
                    elif not args.verbose:
                        print(f"Now playing: {title} by {artist}", file=original_stdout, flush=True)
                    last_logged_song = log_song_to_lastfm(
                        lastfm_network, artist, title,
                        last_logged_song, original_stdout
                    )
                    current_song = song
                    no_song_detected_count = 0
                else:
                    consecutive_same_song_count += 1
                    if args.verbose and not args.tui:
                        print(f"Still playing: {title} by {artist}", file=original_stdout, flush=True)
            else:
                consecutive_same_song_count = 0
                no_song_detected_count += 1
                if no_song_detected_count >= 3:
                    if args.verbose and not args.tui:
                        print("No song detected. Trying aggressive detection...", file=original_stdout, flush=True)

                    # Create a function that will use our pre-recorded audio data
                    async def recognize_func_aggressive(audio_data):
                        return await recognize_song(audio_data, args.verbose, original_stdout)

                    # Pass None as the audio stream since we already have the data
                    song = await aggressive_song_check(recognize_func_aggressive, None, args.verbose, original_stdout)
                    if song and song[0] and song[1] and song[0] != "None" and song[1] != "None":
                        artist, title = song
                        if args.tui:
                            display_tui(song)
                        elif not args.verbose:
                            print(f"Detected after aggressive check: {title} by {artist}", file=original_stdout, flush=True)
                        last_logged_song = log_song_to_lastfm(
                            lastfm_network, artist, title,
                            last_logged_song, original_stdout
                        )
                        current_song = song
                        no_song_detected_count = 0
                    else:
                        if args.tui:
                            display_tui()
                        elif not args.verbose:
                            print("No valid song detected", file=original_stdout, flush=True)
                        current_song = None
            
            await asyncio.sleep(CHECK_INTERVAL)

        except Exception as e:
            print(f"An error occurred: {e}", file=original_stdout, flush=True)

    stream.stop_stream()
    stream.close()
    p.terminate()

if __name__ == "__main__":
    asyncio.run(main())