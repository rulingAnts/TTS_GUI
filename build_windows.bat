@echo off
REM build_windows.bat — Build Kokoro TTS Studio installer for Windows
setlocal EnableDelayedExpansion

set APP_NAME=Kokoro TTS Studio
set INNO_COMPILER=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
set DIST_DIR=dist

echo ╔══════════════════════════════════════════════╗
echo ║   Kokoro TTS Studio — Windows build          ║
echo ╚══════════════════════════════════════════════╝
echo.

REM ── Dependency checks ──────────────────────────────────────────────────
where python >nul 2>&1 || (echo ✗ python not found && exit /b 1)
where pyinstaller >nul 2>&1 || (echo ✗ pyinstaller not found. Run: pip install pyinstaller && exit /b 1)
if not exist "%INNO_COMPILER%" (
    echo ✗ Inno Setup not found at: %INNO_COMPILER%
    echo   Install: choco install innosetup
    echo   Or download from: https://jrsoftware.org/isdl.php
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version') do echo ✓ %%v
echo ✓ PyInstaller found
echo ✓ Inno Setup found
echo.

REM ── PyInstaller ──────────────────────────────────────────────────────────
echo ▶ Running PyInstaller...
pyinstaller kokoro_studio.spec --clean --noconfirm
if errorlevel 1 (echo ✗ PyInstaller failed && exit /b 1)
echo ✓ PyInstaller done
echo.

if not exist "%DIST_DIR%\%APP_NAME%\" (
    echo ✗ Expected output not found: %DIST_DIR%\%APP_NAME%\
    exit /b 1
)

REM ── Inno Setup ───────────────────────────────────────────────────────────
echo ▶ Compiling installer with Inno Setup...
"%INNO_COMPILER%" installer_windows.iss
if errorlevel 1 (echo ✗ Inno Setup failed && exit /b 1)
echo ✓ Installer compiled
echo.

echo ╔══════════════════════════════════════════════╗
echo ║   Build complete ✓                           ║
echo ╚══════════════════════════════════════════════╝
echo   Output: %DIST_DIR%\KokoroTTSStudio-Setup.exe
