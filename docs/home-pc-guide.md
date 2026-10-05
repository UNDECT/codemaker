# 집 PC에서 매크로 돌리고 휴대폰으로 보기 (Tailscale)

서버(데이터센터) IP를 막는 사이트는 집 인터넷으로 접속하면 됩니다.
집 PC에서 매크로를 돌리고, 휴대폰은 Tailscale(무료)로 어디서든 그 PC의 관리 화면에 접속합니다.
**집 PC는 켜 두어야 합니다** (꺼지면 매크로도 멈춤).

## 빠른 설치 (한 줄)

1. 시작 버튼 → **PowerShell** 검색 → 열기
2. 아래 한 줄 붙여넣고 Enter (관리자 확인 창이 뜨면 **예**):
   ```
   irm https://raw.githubusercontent.com/UNDECT/codemaker/ccr-287c0b4e-v2ibub/scripts/setup-windows.ps1 | iex
   ```
   파이썬·글자 인식(한국어)·Tailscale·매크로 프로그램 설치, 바탕화면 바로가기, 켤 때 자동 실행, 절전 끄기까지 합니다.
3. 끝나면 **Tailscale 로그인**(아래 B-1)만 하면 됩니다. 비밀번호는 `codemaker\data\password.txt` 에 있습니다. 다시 실행하면 업데이트(만든 매크로는 유지).

한 줄 설치가 안 되면 아래 A를 따라 직접 설치하세요.

## A. 집 PC 설치 (직접, 15분)

1. **파이썬 설치** — https://www.python.org/downloads/ → 노란 Download 버튼 → 받은 파일 실행
   → 첫 화면 아래 **"Add python.exe to PATH" 체크** → Install Now
2. **(글자 인식을 쓰면) Tesseract 설치** — https://github.com/UB-Mannheim/tesseract/wiki
   → `tesseract-ocr-w64-setup-....exe` 받아 실행 → 설치 중 **Additional language data** 펼쳐서 **Korean** 체크
   → 설치 위치는 기본값 그대로 (프로그램이 알아서 찾음)
3. **프로그램 받기** — https://github.com/UNDECT/codemaker → 초록 **Code** 버튼 → **Download ZIP**
   → 압축 풀기 (예: `문서\codemaker`)
4. 풀린 폴더의 **`start-windows.bat` 더블클릭**
   - 처음엔 설치 때문에 5~10분 걸립니다
   - "Windows의 PC 보호" 파란 창 → **추가 정보 → 실행**
   - "Windows 보안 경고" → **개인·공용 둘 다 체크 → 액세스 허용**
5. 비밀번호는 `data\password.txt` 파일에 있습니다 → 브라우저가 열리면 그 비밀번호로 로그인
6. **절전 끄기** — 설정 → 시스템 → 전원(및 배터리) → 화면 및 절전 → **절전 모드: 안 함**
   (화면은 꺼져도 됩니다. 절전 모드만 '안 함')
7. (선택) **켜면 자동 실행** — `Win + R` → `shell:startup` 입력 → 열린 폴더에
   `start-windows.bat` 을 **마우스 오른쪽 끌기 → 여기에 바로 가기 만들기**

검은 창을 닫으면 매크로도 꺼집니다. 최소화만 해 두세요.

## B. Tailscale 설정 (휴대폰으로 보기, 10분)

1. **PC**: https://tailscale.com/download → Windows 다운로드 → 설치 → 작업 표시줄 Tailscale 아이콘 → **Log in**
   → 구글 계정 등으로 로그인
2. **휴대폰**: 플레이스토어/앱스토어에서 **Tailscale** 설치 → **PC와 같은 계정**으로 로그인
   → "VPN 연결 허용" → **허용**
3. 휴대폰 Tailscale 앱 → 기기 목록에서 **PC 이름** 누르기 → `100.` 으로 시작하는 주소 복사 (예: `100.101.102.103`)
4. 휴대폰 크롬 주소창에 **`http://100.101.102.103:8080`** (복사한 주소 + `:8080`) → 비밀번호 입력
5. 크롬 메뉴(⋮) → **홈 화면에 추가** → 이제 앱처럼 열림

휴대폰에서 볼 때는 **Tailscale 앱이 연결(Active)** 상태여야 합니다. 배터리는 거의 안 씁니다.
관리 화면은 **Tailscale 주소에만** 열립니다 (같은 와이파이의 다른 기기나 인터넷에서는 안 보임). PC에서 볼 때도 같은 `100.` 주소를 씁니다.
Tailscale에 로그인하지 않은 상태로 실행하면 그 PC 안(`127.0.0.1:8080`)에서만 열립니다.

## C. 서버에서 쓰던 매크로 옮기기

1. 서버(Termius)에서 `cat ~/codemaker/data/config.yaml` → 나온 내용 전체 복사
2. 집 PC 관리 화면 → **설정 → 고급: 설정 파일 직접 편집** → 내용 전부 지우고 붙여넣기 → **검사하고 저장**
3. 사이트에 로그인이 필요하면 **브라우저 탭에서 로그인 → 로그인 상태 저장** 을 집 PC에서 다시 한 번
4. 같은 매크로를 두 곳에서 돌리지 않도록 **서버 쪽 매크로는 중지**

## 안 될 때

| 증상 | 해결 |
|---|---|
| 휴대폰에서 주소가 안 열림 | 휴대폰·PC 모두 Tailscale 연결 확인 / PC의 검은 창이 켜져 있는지 / `:8080` 붙였는지 |
| 휴대폰에서만 안 열림 (PC에선 열림) | 방화벽: 관리자 PowerShell 에서 `New-NetFirewallRule -DisplayName 'webmacro panel (Tailscale only)' -Direction Inbound -Protocol TCP -LocalPort 8080 -RemoteAddress 100.64.0.0/10 -Action Allow -Profile Any` |
| 그래도 안 열림 | PC: 제어판 → Windows Defender 방화벽 → 앱 허용 → **Python** 의 개인·공용 체크 |
| "파이썬이 없습니다" | A-1에서 "Add python.exe to PATH" 체크 안 함 → 파이썬 다시 설치하며 체크 |
| 설치 중 오류 | 검은 창 캡처해서 보내기 |
| 비밀번호 잊음 | `codemaker\data\password.txt` 열기 |
| 업데이트 | 새 ZIP 받아 풀고, 예전 폴더의 `data` 폴더를 새 폴더로 복사 → `start-windows.bat` |
