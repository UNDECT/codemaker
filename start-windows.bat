@echo off
chcp 65001 >nul
title webmacro
cd /d "%~dp0"

rem 윈도우에서 처음 써보기: 이 파일을 더블클릭하세요.
rem 처음 한 번은 설치 때문에 몇 분 걸립니다. 다음부터는 바로 열립니다.

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
set "PY=.venv\Scripts\python.exe"

if not exist ".venv\installed.txt" (
  echo [2/3] 필요한 프로그램 설치 중... ^(몇 분 걸립니다^)
  "%PY%" -m pip install --upgrade pip >nul
  "%PY%" -m pip install -r requirements.txt || goto :fail
  echo [3/3] 크롬 브라우저 받는 중...
  "%PY%" -m playwright install chromium || goto :fail
  echo ok> ".venv\installed.txt"
)

if not exist data mkdir data
rem 휴대폰(Tailscale)에서 접속할 수 있게 비밀번호를 처음 한 번 만든다
if not exist "data\password.txt" (
  "%PY%" -c "import secrets;print(secrets.token_hex(5))" > "data\password.txt" || goto :fail
)
set /p WEBMACRO_PANEL_PASSWORD=<"data\password.txt"
echo.
echo  ===========================================================
echo   이 PC에서:   http://127.0.0.1:8080
echo   휴대폰에서:  http://[Tailscale 앱에 나온 이 PC 주소]:8080
echo   비밀번호:    %WEBMACRO_PANEL_PASSWORD%
echo   (이 창을 닫으면 매크로도 꺼집니다)
echo  ===========================================================
echo  처음 실행 때 "Windows 보안 경고"가 뜨면 [액세스 허용]을 누르세요.
echo  화면 글자 인식(OCR)을 쓰려면 Tesseract를 따로 설치하세요 (README 참고).
echo.
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8080"
"%PY%" -m webmacro panel data\config.yaml --host 0.0.0.0
pause
exit /b 0

:fail
echo.
echo [!] 설치 중 오류가 났습니다. 위 메시지를 캡처해서 보내주세요.
pause
exit /b 1
