

  

<div align="center">

# VinylPi

## Automatically track your vinyl listening via Last.fm


<a href="">[![Discord](https://img.shields.io/discord/1333199316205961328?logo=discord&logoColor=white)
](https://discord.gg/9Hgvbh8UCn)</a>

</div>

## Disclaimer

This project was created partially using AI, There could be persisting errors, Please join the Discord if you'd like to help.

  

### Default configuration

By default, Songs are checked 3 times for 3 seconds each, If 2 of the songs detected are the same, The song will get logged to Last.fm. You can change these variables in the "vinylpi_lib.py" file, Most of the main logic and variables are contained in this file.

  

If a "lastfm_user_info.json" is not present in the directory, You will need to fill out the Last.FM api information, The script will prompt you for this and once you enter it will be saved. This may pose a security threat as your password will be saved as a hash in the json file, Please be careful and only install this on trusted machines.

  

### Dependencies

Currently this script is designed to run on a Raspberry Pi (Tested on a Pi 3 and Pi 5). It should work on most linux distributions with.

```
sudo apt-get update
sudo apt-get install python3 python3-pip portaudio19-dev ffmpeg libasound2-dev git

```

### Install instructions

First, go to [Last.FM API Create](https://www.last.fm/api/account/create) And fill out the form (You do not have to have a Callback URL or Application Homepage, You can leave that empty) Once created save the information to a safe place it will be needed for the script.

```
git clone https://github.com/powerpcme/VinylPi.git

cd VinylPi

pip install -r requirements.txt --break-system-packages

python VinylPi.py
```

Run the script once to configure it, It should run you though applying the API credentials as well as your Lastfm login. 

### Running options 

-h  - Help command 
-v or --verbose - This is useful for debugging, In case no songs are being detected.
-l or --list-devices - List current audio input devices
-d or --device - Use the number given to the device from the list command here, If this is not specified, It will use the first USB audio device it finds for input. 
-t or --tui - If you do not plan on using this headless, This will provide a nice terminal UI displaying the currently playing song


### Command examples

```
python VinylPi.py -v - Verbose output
python VinylPi.py -l - List avalible devices for use
python VinylPi.py -d 1 - Using the number assigned to the device from the list command, this will set the script to use that device
python VinylPi.py -t - Use the TUI interface to see what tracks are being played
```

### Current bugs 

Right now when the script is ran, A lot of ALSA garbage is displayed but cleared after a bit, Unsure how to fix this but not really program breaking.