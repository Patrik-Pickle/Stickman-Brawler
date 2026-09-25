@echo off
setlocal enabledelayedexpansion
REM ==========================================================================
REM Builds standalone Windows .exe files for Stickman Brawler.
REM
REM Run this ONCE, on Windows, from inside the folder containing client.py,
REM server.py, stickman_brawler.py, game_common.py, and README_DIST.txt.
REM You (the builder) need Python installed for this step - but the .exe
REM files it produces can be shared with anyone and need NO Python install
REM on their end.
REM ==========================================================================

echo Checking Python is available...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: "python" was not found on your PATH.
    echo Install Python from https://python.org/downloads - during setup,
    echo make sure you tick "Add python.exe to PATH" - then run this
    echo script again.
    echo.
    pause
    exit /b 1
)
python --version

echo.
echo Installing/upgrading PyInstaller and pygame...
REM Using "python -m pip" / "python -m PyInstaller" instead of the bare
REM "pip"/"pyinstaller" commands avoids the #1 cause of this script
REM silently doing nothing: PyInstaller getting installed somewhere not
REM on your PATH. Going through "python -m" guarantees we use whichever
REM Python just installed it.
python -m pip install --upgrade pip pyinstaller pygame
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed - see the output above for why.
    echo Common causes: no internet connection, or you need to run this
    echo terminal "as Administrator".
    pause
    exit /b 1
)

echo.
echo Confirming PyInstaller actually installed...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller still isn't runnable after installing it.
    echo Try closing this window, opening a NEW terminal ^(so PATH changes
    echo take effect^), and running this script again.
    pause
    exit /b 1
)

echo.
echo Building the ONLINE CLIENT (what each player runs)...
python -m PyInstaller --onefile --noconsole --name StickmanBrawlerClient client.py
if errorlevel 1 (
    echo.
    echo ERROR: building the client failed - see the PyInstaller output above.
    pause
    exit /b 1
)

echo.
echo Building the SERVER (whoever is hosting runs this)...
python -m PyInstaller --onefile --name StickmanBrawlerServer server.py
if errorlevel 1 (
    echo.
    echo ERROR: building the server failed - see the PyInstaller output above.
    pause
    exit /b 1
)

echo.
echo Building the LOCAL/HOTSEAT version (optional, same-keyboard play)...
python -m PyInstaller --onefile --noconsole --name StickmanBrawlerLocal stickman_brawler.py
if errorlevel 1 (
    echo.
    echo ERROR: building the local version failed - see the PyInstaller output above.
    pause
    exit /b 1
)

echo.
echo Copying sprites/music/sfx into the dist folder...
xcopy /E /I /Y sprites dist\sprites >nul
xcopy /E /I /Y music dist\music >nul
xcopy /E /I /Y sfx dist\sfx >nul

echo.
echo Copying README.txt into the dist folder...
if not exist "README_DIST.txt" (
    echo   WARNING: README_DIST.txt not found next to this script - skipping.
    echo   ^(Players won't get a README.txt in dist\ this time.^)
) else (
    copy /Y README_DIST.txt dist\README.txt >nul
)

echo.
echo Verifying the .exe files actually exist...
set ALL_GOOD=1
if not exist "dist\StickmanBrawlerClient.exe" (
    echo   MISSING: dist\StickmanBrawlerClient.exe
    set ALL_GOOD=0
)
if not exist "dist\StickmanBrawlerServer.exe" (
    echo   MISSING: dist\StickmanBrawlerServer.exe
    set ALL_GOOD=0
)
if not exist "dist\StickmanBrawlerLocal.exe" (
    echo   MISSING: dist\StickmanBrawlerLocal.exe
    set ALL_GOOD=0
)

echo.
if "!ALL_GOOD!"=="1" (
    echo ==========================================================================
    echo SUCCESS! Everything you need to share is in the "dist" folder:
    echo   - StickmanBrawlerClient.exe
    echo   - StickmanBrawlerServer.exe
    echo   - StickmanBrawlerLocal.exe
    echo   - sprites\, music\, sfx\
    echo   - README.txt ^(how to run, play, and set up online/internet play^)
    echo.
    echo Zip up the whole "dist" folder and send it - no Python needed to run it.
    echo ==========================================================================
) else (
    echo ==========================================================================
    echo Something didn't build correctly - see MISSING lines above and the
    echo PyInstaller output earlier in this window for the actual error.
    echo ==========================================================================
)
pause
