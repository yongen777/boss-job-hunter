@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo.
echo   BOSS直聘求职猎手 - 启动专用浏览器
echo   ------------------------------------
echo   这个窗口用的是独立身份，登录态存在 data\browser-profile 里，
echo   跟你日常用的浏览器互不干扰。整个文件夹拷走，登录态也跟着走。
echo.

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY goto nopython

%PY% -m huntsman.browser
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo   [x] 启动失败，返回码 %RC%
pause
exit /b %RC%

:nopython
echo   [x] 没找到 Python，先装 Python 3.9 以上再来。
echo.
pause
exit /b 1