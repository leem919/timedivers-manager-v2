# Timedivers Manager V2
Version manager for Steam games
Based on [Timedivers Manager](https://github.com/leem919/timedivers-manager)

# Setup
1. In the Steam properties of the game you wish to downgrade, set the game to only update when you launch it.
2. Take note of whether or not you're opted into a beta version and check which one it is.
3. Create a new folder anywhere and place timediversvermanv2.exe into it. You can grab that from the [releases](https://github.com/leem919/timedivers-manager-v2/releases) or build it yourself.
4. Open Microsoft Edge, go to [steamdb.info](https://steamdb.info) and log into your steam account. Make sure to check 'Remember Me'.
5. Run timediversvermanv2.exe, select the game, and update the manifests.
6. Browse and navigate to the steamapps folder that contains the Common folder. (Don't select the Common folder itself)
7. Select a beta, if necessary, and then download a version. You can use the Steam Console or the Depot Downloader (Instructions below).
8. If using the Steam Console, check if any depots are already downloaded, they might be for a different version and should be deleted to be safe. 
9. Select "Open Steam Console" and wait for the Steam window to open and switch to the console tab.
10. Select "Copy" next to the first command, paste it into the console, and hit enter to start the download.
11. Wait for the console to say the depot download is complete, then repeat for the next depots.
12. Once the console says they're all downloaded, refresh and then select "Import Version".

# Things to Know
1. Always check for game updates on Steam. If an update comes out, make sure that the Steam version is active in the version manager and then download the update. Updates cannot be easily skipped, and downloading an update while an old version is active will cause issues.
2. It is recommended to switch back to the steam version when not actively playing for a while in case steam does a file check.
3. The scraping process for updating the list may appear stuck at some points. If it appears stuck for longer than a minute or two, close everything and try again.
4. This program does not prompt you for, or store, your password. That is all handled with the Depot Downloader and choosing to remember your password just passes the remember-password flag to it.

# Depot Downloader
The Depot Downloader is a little slower, but it's also a dedicated tool for downloading depots and will show you the download progress.
1. Download the latest windows-x64 version of the [Depot Downloader](https://github.com/SteamRE/DepotDownloader/releases) and place the exe in the same folder as the version manager.
2. Enter your Steam username and choose whether or not the downloader should remember your password.
3. Select a version and then download using the Depot Downloader.
4. You will be prompted for your Steam password. Account authentication is necessary for getting your licenses.

# Building
1. Install Python 3 from [python.org](https://python.org)
2. Install the dependencies with `pip install -r requirements.txt`
3. Run `pyinstaller --onefile --clean --noconsole --add-data "blackhole.ico;." --icon=blackhole.ico timediversverman.py`

<img width="1122" height="750" alt="Screenshot" src="https://i.imgur.com/ofQgAih.png"/>
