@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo.
echo   BOSS直聘求职猎手 - 本地桥
echo   ------------------------------------
echo.

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY goto nopython

%PY% -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if errorlevel 1 goto oldpython

%PY% "bridge\bridge.py"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo   桥退出了，返回码 %RC%
pause
exit /b %RC%

:nopython
echo   [x] 没找到 Python。
echo.
echo       装一个 Python 3.9 以上（python.org 下载，安装时勾上 Add to PATH），
echo       或者如果你用的是 Anaconda，从 Anaconda Prompt 里跑这个脚本。
echo.
pause
exit /b 1

:oldpython
echo   [x] 这个 Python 版本太老，需要 3.9 以上。
echo.
%PY% --version
echo.
pause
exit /b 1