@echo off
setlocal
cd /d "%~dp0"

python -B -m PyInstaller ^
  --clean ^
  --onefile ^
  --console ^
  --name task_worker ^
  --distpath dist ^
  --workpath build\pyinstaller ^
  --specpath build\pyinstaller ^
  --exclude-module pandas ^
  --exclude-module numpy ^
  --exclude-module pyarrow ^
  --exclude-module numba ^
  --exclude-module llvmlite ^
  --exclude-module openpyxl ^
  --exclude-module PIL ^
  --exclude-module lxml ^
  --exclude-module scipy ^
  --exclude-module matplotlib ^
  --exclude-module IPython ^
  --exclude-module pytest ^
  task_worker.py
if errorlevel 1 exit /b %errorlevel%

if not exist "dist" mkdir "dist"
copy /Y "config.json" "dist\config.json" >nul

echo.
echo Build complete:
echo   dist\task_worker.exe
echo   dist\config.json
echo.
echo Copy both files to the other computer, keep them in the same folder, then run task_worker.exe.
