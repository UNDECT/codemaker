# 집 PC(윈도우 10/11) 한 번에 설치: 파이썬 · 글자 인식(Tesseract+한국어) · Tailscale · 매크로 프로그램
# 사용법: PowerShell 을 열고 아래 한 줄 붙여넣기
#   irm https://raw.githubusercontent.com/UNDECT/codemaker/ccr-287c0b4e-v2ibub/scripts/setup-windows.ps1 | iex
# 다시 실행해도 됩니다(업데이트). 만든 매크로·비밀번호(data 폴더)는 그대로 둡니다.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$Branch = 'ccr-287c0b4e-v2ibub'
$Dest = Join-Path $HOME 'codemaker'

function Say($t) { Write-Host ""; Write-Host "▶ $t" -ForegroundColor Cyan }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Has-Winget { [bool](Get-Command winget -ErrorAction SilentlyContinue) }
function Winget-Install($id, $extra) {
  # 이미 있으면 건너뜀. 관리자 확인 창이 뜨면 [예]
  winget list -e --id $id --accept-source-agreements *> $null
  if ($LASTEXITCODE -eq 0) { Write-Host "  이미 설치됨: $id"; return }
  $wa = @('install', '-e', '--id', $id, '--accept-package-agreements', '--accept-source-agreements', '--silent') + $extra
  & winget @wa
  if ($LASTEXITCODE -ne 0) {   # -e 는 대소문자까지 같아야 해서, 못 찾으면 한 번 더 느슨하게
    $wa = @('install', '--id', $id, '--accept-package-agreements', '--accept-source-agreements', '--silent') + $extra
    & winget @wa
  }
  if ($LASTEXITCODE -ne 0) { Write-Host "  [!] $id 설치가 끝나지 않았습니다 (코드 $LASTEXITCODE)" -ForegroundColor Yellow }
}
function Find-Python {
  foreach ($c in @('python', 'py')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike '*WindowsApps*') { return $cmd.Source }   # 스토어 바로가기(가짜) 제외
  }
  $p = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
       Sort-Object FullName -Descending | Select-Object -First 1
  if ($p) { return $p.FullName }
  return $null
}

try {
  if (-not (Has-Winget)) {
    throw "winget(앱 설치 관리자)이 없습니다. Microsoft Store 에서 '앱 설치 관리자'를 업데이트한 뒤 다시 실행하세요."
  }

  Say "1/6 파이썬"
  if (-not (Find-Python)) { Winget-Install 'Python.Python.3.12' @('--scope', 'user') }
  Refresh-Path
  $Py = Find-Python
  if (-not $Py) { throw "파이썬을 찾을 수 없습니다. PowerShell 을 닫고 다시 열어 같은 줄을 한 번 더 실행하세요." }
  Write-Host "  $Py"

  Say "2/6 글자 인식 (Tesseract)"
  Winget-Install 'UB-Mannheim.TesseractOCR' @()

  Say "3/6 Tailscale (휴대폰 연결용)"
  Winget-Install 'tailscale.tailscale' @()

  Say "4/6 매크로 프로그램 받기 → $Dest"
  $zip = Join-Path $env:TEMP 'codemaker.zip'
  $tmp = Join-Path $env:TEMP 'codemaker-unzip'
  Invoke-WebRequest "https://github.com/UNDECT/codemaker/archive/refs/heads/$Branch.zip" -OutFile $zip -UseBasicParsing
  if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
  Expand-Archive $zip $tmp -Force
  $src = Get-ChildItem $tmp -Directory | Select-Object -First 1
  New-Item -ItemType Directory -Force $Dest | Out-Null
  # data(설정·비밀번호·로그인 상태)와 .venv 는 남기고 나머지만 새로 덮어쓴다
  robocopy $src.FullName $Dest /E /XD data .venv tessdata /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "파일 복사 실패 (robocopy $LASTEXITCODE)" }
  Remove-Item $tmp -Recurse -Force; Remove-Item $zip -Force
  Remove-Item (Join-Path $Dest '.venv\installed.txt') -ErrorAction SilentlyContinue   # 업데이트면 필요한 프로그램 다시 확인
  if (-not (Test-Path (Join-Path $Dest '.venv\Scripts\python.exe'))) {
    Write-Host "  실행 환경 만드는 중..."
    & $Py -m venv (Join-Path $Dest '.venv')
    if ($LASTEXITCODE -ne 0) { throw "파이썬 실행 환경을 만들지 못했습니다" }
  }

  Say "5/6 한국어 글자 데이터"
  $td = Join-Path $Dest 'tessdata'
  New-Item -ItemType Directory -Force $td | Out-Null
  foreach ($l in @('kor', 'eng')) {
    $f = Join-Path $td "$l.traineddata"
    if (-not (Test-Path $f)) {
      Invoke-WebRequest "https://github.com/tesseract-ocr/tessdata_fast/raw/main/$l.traineddata" -OutFile $f -UseBasicParsing
    }
  }

  Say "6/6 방화벽 · 바로가기 · 절전 끄기"
  # 휴대폰(Tailscale 100.64.0.0/10)에서 오는 8080 연결만 허용. 관리자 확인 창이 뜨면 [예]
  $rule = 'webmacro panel (Tailscale only)'
  if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
    $fw = "New-NetFirewallRule -DisplayName '$rule' -Direction Inbound -Protocol TCP -LocalPort 8080 -RemoteAddress 100.64.0.0/10 -Action Allow -Profile Any"
    try { Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $fw; Write-Host "  방화벽: Tailscale 에서 오는 8080 허용" }
    catch { Write-Host "  [!] 방화벽 규칙을 못 만들었습니다 (관리자 확인 거절?)" -ForegroundColor Yellow }
  }
  $bat = Join-Path $Dest 'start-windows.bat'
  $ws = New-Object -ComObject WScript.Shell
  foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Startup'))) {
    $lnk = $ws.CreateShortcut((Join-Path $dir '업무 매크로.lnk'))
    $lnk.TargetPath = $bat; $lnk.WorkingDirectory = $Dest; $lnk.WindowStyle = 7   # 최소화로 시작
    $lnk.Save()
  }
  Write-Host "  바탕화면 바로가기 + 켤 때 자동 실행 등록"
  try { powercfg /change standby-timeout-ac 0 | Out-Null; powercfg /change hibernate-timeout-ac 0 | Out-Null; Write-Host "  전원 연결 시 절전 안 함" }
  catch { Write-Host "  [!] 절전 설정은 직접 꺼 주세요 (설정 → 시스템 → 전원)" -ForegroundColor Yellow }

  Write-Host ""
  Write-Host "===========================================================" -ForegroundColor Green
  Write-Host " 설치 끝. 이제 남은 건:" -ForegroundColor Green
  Write-Host "  1) 작업 표시줄 오른쪽 Tailscale 아이콘 → Log in (휴대폰과 같은 계정)"
  Write-Host "  2) 비밀번호는 $Dest\data\password.txt 에 있습니다"
  Write-Host "  3) '보안 경고'가 뜨면 개인·공용 체크 → 액세스 허용"
  Write-Host "===========================================================" -ForegroundColor Green
  Start-Process -FilePath $bat -WorkingDirectory $Dest
  try { Start-Process 'C:\Program Files\Tailscale\tailscale-ipn.exe' -ErrorAction Stop } catch {}
}
catch {
  Write-Host ""
  Write-Host "[!] 설치가 멈췄습니다: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "    이 창을 캡처해서 보내주세요."
}
