import argparse
import sys
import os
import asyncio
import pyaudio
import shutil

from vinylpi_lib import (
    CHUNK, FORMAT, CHANNELS, RATE, CHECK_INTERVAL,
    get_usb_audio_device, get_lastfm_network, clear_console,
    aggressive_song_check, check_song_consistency, recognize_song,
    log_song_to_lastfm, list_audio_devices
)

def create_parser():
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

# Store the original stdout
original_stdout = sys.stdout

# Redirect stderr to /dev/null if not in verbose mode
if not args.verbose:
    sys.stderr = open(os.devnull, 'w')

def display_tui(current_song=None, is_listening=True):
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

async def main():
    if args.list_devices:
        list_audio_devices()
        sys.exit(0)

    if args.device is not None:
        selected_device = args.device
        if args.verbose:
            print(f"Using user-specified audio device index: {selected_device}", file=original_stdout)
    else:
        selected_device = get_usb_audio_device()
        if selected_device is None:
            print("No USB audio device found. Exiting.", file=original_stdout)
            sys.exit(1)
        if args.verbose:
            print(f"Using USB audio device index: {selected_device}", file=original_stdout)

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

    while True:
        try:
            if args.tui:
                display_tui(current_song)
            elif args.verbose:
                print("Starting song detection...", file=original_stdout, flush=True)

            async def recognize_func(audio_data):
                return await recognize_song(audio_data, args.verbose, original_stdout)

            song = await check_song_consistency(recognize_func, stream, args.verbose, original_stdout)

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

                    async def recognize_func_aggressive(audio_data):
                        return await recognize_song(audio_data, args.verbose, original_stdout)

                    song = await aggressive_song_check(recognize_func_aggressive, stream, args.verbose, original_stdout)
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
