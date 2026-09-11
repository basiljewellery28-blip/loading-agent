@echo off
setlocal
title Agent 3: The Plate Loader (LP Agent)
color 0E

echo ==============================================================================
echo   STARTING AGENT 3: THE PLATE LOADER (LP Agent) - STANDALONE
echo ==============================================================================
echo.

:: Ensure current working directory is the LP Agent folder
cd /d "%~dp0"

:: Activate virtual environment if available (Local SSD or USB root)
if not defined VIRTUAL_ENV (
    if exist "C:\cadprod_venv\Scripts\activate.bat" (
        call "C:\cadprod_venv\Scripts\activate.bat"
    ) else if exist "%~dp0..\.venv\Scripts\activate.bat" (
        call "%~dp0..\.venv\Scripts\activate.bat"
    ) else if exist "%~dp0.venv\Scripts\activate.bat" (
        call "%~dp0.venv\Scripts\activate.bat"
    )
)

:: Auto-detect Autodesk Netfabb console runner (2027 primary, 2026 fallback)
if exist "C:\Program Files\Autodesk\Netfabb 2027\netfabb_console.exe" (
    set "NETFABB_CONSOLE_PATH=C:\Program Files\Autodesk\Netfabb 2027\netfabb_console.exe"
) else if exist "C:\Program Files\Autodesk\Netfabb 2026\netfabb_console.exe" (
    set "NETFABB_CONSOLE_PATH=C:\Program Files\Autodesk\Netfabb 2026\netfabb_console.exe"
)

:: Ensure Q:\ drive is transitioned online
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-CimMethod -Namespace 'root\cimv2' -ClassName 'Win32_OfflineFilesCache' -MethodName 'TransitionOnline' -Arguments @{ Path = '\\192.1.1.131\cad'; Flags = [uint32]0 } -ErrorAction SilentlyContinue | Out-Null } catch {}"

echo [1] Launching LP Agent Visual Staging UI on port 4200...
echo [2] Opening browser at http://localhost:4200...
echo.
echo Leave this window open while you work. Close it to stop the server.
echo ==============================================================================
echo.

:: Launch the standalone LP Agent UI
python agent_entry.py ui --port 4200

:: In case the server terminates or crashes, pause so errors can be inspected
pause
