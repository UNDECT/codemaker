@echo off
chcp 65001 >nul
title webmacro
cd /d "%~dp0"

rem 윈도우에서 처음 써보기: 이 파일을 더블클릭하세요.
rem 처음 한 번은 설치 때문에 몇 분 걸립니다. 다음부터는 바로 열립니다.

rem 실행 환경(.venv)이 이미 있으면 파이썬 확인은 건너뛴다
if exist ".venv\Scripts\python.exe" goto :haspy
where python >nul 2>nul
if errorlevel 1 (
  echo [!] 파이썬이 없습니다.
  echo     https://www.python.org/downloads/ 에서 설치하세요.
  echo     설치 첫 화면에서 "Add python.exe to PATH" 를 꼭 체크하세요.
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 실행 환경 만드는 중...
  python -m venv .venv || goto :fail
)
:haspy
set "PY=.venv\Scripts\python.exe"
rem 설치 스크립트가 받아 둔 한국어 글자 데이터
if exist "%~dp0tessdata\kor.traineddata" set "TESSDATA_PREFIX=%~dp0tessdata"

if not exist ".venv\installed.txt" (
  echo [2/3] 필요한 프로그램 설치 중... ^(몇 분 걸립니다^)
  "%PY%" -m pip install --upgrade pip >nul
  "%PY%" -m pip install -r requirements.txt || goto :fail
  echo [3/3] 크롬 브라우저 받는 중...
  "%PY%" -m playwright install chromium || goto :fail
  echo ok> ".venv\installed.txt"
)

if not exist data mkdir data
rem 휴대폰(Tailscale)에서 접속할 수 있게 긴 비밀번호(128비트)를 처음 한 번 만든다
if not exist "data\password.txt" (
  "%PY%" -c "import secrets;print(secrets.token_hex(16))" > "data\password.txt" || goto :fail
)
set /p WEBMACRO_PANEL_PASSWORD=<"data\password.txt"

rem 관리 화면은 Tailscale 주소에만 연다 (같은 와이파이의 다른 기기나 인터넷에서는 안 보임)
rem PC를 막 켰을 때는 Tailscale 연결이 늦을 수 있어 최대 60초 기다린다
set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
set "HOST="
set /a TRIES=0
:waitts
if exist "%TS%" for /f "usebackq delims=" %%i in (`"%TS%" ip -4 2^>nul`) do if not defined HOST set "HOST=%%i"
if defined HOST goto :gothost
set /a TRIES+=1
if %TRIES% GEQ 12 goto :gothost
echo  Tailscale 연결 기다리는 중... (%TRIES%/12)
timeout /t 5 /nobreak >nul
goto :waitts
:gothost
if not defined HOST (
  echo  [!] Tailscale 에 로그인돼 있지 않아 이 PC 안에서만 열립니다. 작업 표시줄 Tailscale 아이콘 → Log in 후 다시 실행하세요.
  set "HOST=127.0.0.1"
)

echo.
echo  ===========================================================
echo   관리 화면:  http://%HOST%:8080   (휴대폰도 이 주소, Tailscale 켜고)
echo   비밀번호:   data\password.txt 파일 안에 있습니다
echo   (이 창을 닫으면 매크로도 꺼집니다)
echo  ===========================================================
echo  처음 실행 때 "Windows 보안 경고"가 뜨면 [액세스 허용]을 누르세요.
echo.
start "" cmd /c "timeout /t 4 >nul & start http://%HOST%:8080"
"%PY%" -m webmacro panel data\config.yaml --host %HOST%
pause
exit /b 0

:fail
echo.
echo [!] 설치 중 오류가 났습니다. 위 메시지를 캡처해서 보내주세요.
pause
exit /b 1
