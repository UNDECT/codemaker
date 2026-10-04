# webmacro — 웹사이트 무인 매크로

웹사이트 화면에서 **색상을 찾아 클릭**하고, **화면 글자를 읽어(OCR) 입력**하며, **상황에 따라 다른 액션패턴**(클릭·입력 순서)을 실행해
업무를 끝내는 매크로입니다. 화면 없는 브라우저(Chromium)로 동작하므로 **클라우드 서버에서 24시간**
돌릴 수 있고, 집 PC를 꺼둬도 됩니다.

```
[몇 초마다] 화면 캡처 → 규칙을 위에서부터 검사 → 처음 맞는 규칙의 액션패턴 실행
                                      └ 아무것도 안 맞으면 아무것도 누르지 않음
```

## 처음 써보기 (5분)

**윈도우**: 저장소를 받아(초록 Code 버튼 → Download ZIP → 압축 풀기) `start-windows.bat` 더블클릭.
**맥·리눅스**: `bash start.sh`

처음 한 번은 설치 때문에 몇 분 걸리고, 끝나면 브라우저에 관리 화면(http://127.0.0.1:8080)이 열립니다.
처음 실행하면 **연습용 결재함** 페이지와 그걸 처리하는 규칙이 자동으로 설정되어 있어서,
**현황 탭 → ▶ 시작**만 누르면 매크로가 빨간 '결재' 버튼 → 초록 '확인'을 눌러 5건을 처리하고 스스로 멈추는 걸 볼 수 있습니다.
(연습 페이지는 '화면' 탭에서 볼 수 있고, 새로고침하면 다시 5건이 채워집니다.)

그다음 실제 사이트로 바꾸려면: **설정 탭**에서 사이트 주소 입력 → **화면 탭**에서 색·영역을 눌러 조건 만들기 → 시험 → 시작.
화면 글자 인식(OCR)은 Tesseract를 따로 설치해야 합니다(아래 OCR 항목).

## 0. 관리 화면 (휴대폰·PC)

```bash
python -m webmacro panel data/config.yaml          # → http://127.0.0.1:8080 (설정 파일이 없으면 새로 만듦)
```

아래 탭 3개로 나뉘어 있고, 휴대폰 화면에 맞춰져 있습니다. 브라우저 메뉴에서 **"홈 화면에 추가"**를 하면 앱처럼 열립니다.

| 탭 | 내용 |
|---|---|
| **◉ 현황** | **지금 하는 일**(예: `'빨간 결재 버튼' · 결재처리 2/5 단계: 입력 '처리완료'`), 실행 시간, 마지막 화면 확인 시각 · **오늘 처리/오류/확인 건수**와 규칙별 건수, 지난 2주 기록 · **실시간 화면**(3초마다) · **진행 기록**(시작·규칙 실행·완료·읽은 값·오류·일시정지) · **최근 오류**와 그때 화면 · 저장된 화면 모음 · ▶ 시작 / 시험 / ■ 중지 |
| **▣ 화면** | 서버 브라우저 화면 (**두 손가락으로 확대·이동**, ＋/－ 버튼, PC는 Ctrl+휠). **선택**: 누르면 좌표·색, 드래그하면 영역 (영역이 글자를 자르면 ⚠ 경고 + **"영역 자동 맞춤"**) → "+ 색 조건 / + 좌표 클릭 / + 글자(OCR) 조건 / + 글자 읽기 단계" · **조작**: 누르면 서버 브라우저에서 실제 클릭 → 여기서 사이트에 로그인하고 "로그인 상태 저장" · **지금 화면 판정**: 어떤 규칙이 맞는지 미리 확인 |
| **⚙ 설정** | 사이트 주소·화면 크기·확인 주기, 규칙·액션패턴 편집(저장 시 검사), 로그 |

- 비밀번호(`WEBMACRO_PANEL_PASSWORD`)를 정하면 로그인 화면이 나오고, 한 번 로그인하면 그 기기에서 **30일 유지**됩니다. 5분 안에 5번 틀리면 잠시 막힙니다.
- 오늘 처리 건수는 서버가 재시작돼도 유지되고, 실행 중이던 매크로는 재부팅 후 자동으로 이어서 시작합니다.
- 실행 중에도 현황·화면 보기는 되고, 화면 조작은 중지 후에만 됩니다(매크로와 충돌 방지).

## 1. 설정 파일 (YAML)

관리 화면의 "3. 규칙·액션패턴" 칸에 들어가는 내용입니다.

`examples/example.yaml` 을 복사해서 고칩니다. 핵심은 두 부분입니다.

**액션패턴** — 클릭·입력 순서

```yaml
patterns:
  결재완료:
    - {do: click_match}                 # 조건에서 찾은 색상 위치 클릭
    - {do: wait, sec: 1}
    - {do: click, x: 960, y: 640}       # 고정 좌표 클릭
    - {do: type, text: "처리완료"}
    - {do: press, key: Enter}
```

**규칙** — 어떤 상황에 어떤 패턴을 쓸지 (위에 있을수록 우선)

```yaml
rules:
  - name: 노란 버튼 + '반려' 글자
    when:
      - {color: "#FBC02D", as: 노란버튼}
      - {text: 반려 요청}
    then: 반려처리
  - name: 빨간 버튼
    when:
      - {color: "#E53935", tolerance: 20, region: [0, 150, 1920, 900], min_pixels: 50}
    then: 결재완료
  - name: 할 일 없음
    when: {text: 처리할 항목이 없습니다}
    then: [{do: screenshot, name: done}]
    after: stop           # stop=업무 종료 / continue=계속 감시(기본) / pause=잠시 쉼
```

### 조건 (`when`, 모두 만족해야 실행)

| 조건 | 의미 | 추가 옵션 |
|---|---|---|
| `color: "#RRGGBB"` | 화면에 그 색이 있음 | `tolerance`(허용 오차, 기본 10), `region: [x1,y1,x2,y2]`, `min_pixels`(기본 20), `pick: largest/topmost/leftmost/first`, `as: 이름` |
| `text: 글자` | 페이지에 그 글자가 보임 | |
| `selector: "CSS"` | 그 요소가 보임 | |
| `url: 문자열` | 현재 주소에 포함됨 (예: `/login`) | |
| `ocr: 글자` | **화면 글자 인식**으로 그 글자가 보임 (공백 무시). 이미지·캔버스 글자도 됨 | `region`(권장), `regex: true`(정규식, 괄호 = 뽑을 값), `as: 이름`(뽑은 값 저장), `chars`, `psm`, `lang` |
| 아무 조건에 `absent: true` | **없을 때** 만족 | |

### 동작 (`do`)

| 동작 | 설명 |
|---|---|
| `click_match` | 조건에서 찾은 색 위치 클릭. `target: 이름`(as로 붙인 이름), `dx`/`dy` 보정, `double`, `button` |
| `click` | 좌표 클릭 `x`, `y` |
| `click_selector` / `click_text` | CSS 요소 / 글자를 찾아 클릭 |
| `type` | 글자 입력. `selector` 지정 시 그 칸에 입력. `{env:이름}` → 환경변수 값(비밀번호용), `{var:이름}` → OCR로 읽은 값 |
| `press` | 키 입력 (`Enter`, `Tab`, `Control+A` …) |
| `wait` | `sec` 초 대기 |
| `wait_color` | 색이 나타날 때까지 대기(`timeout`), `as: 이름`으로 위치 저장 → 다음 `click_match`에서 사용 |
| `wait_text` | 글자가 나타날 때까지 대기 |
| `read` | **영역 글자를 읽어 변수에 저장** `region`, `as`. `pattern`(정규식)으로 필요한 부분만. 못 읽으면 실패 처리(엉뚱한 값 입력 안 함) |
| `wait_ocr` | 화면 글자(OCR)가 나타날 때까지 대기 |
| `scroll` `goto` `reload` | 스크롤 / 주소 이동 / 새로고침 |
| `screenshot` `notify` | 화면 저장(output/) / 휴대폰 알림 |
| `run` | 다른 패턴 실행 |
| `stop` | 업무 종료 |

### 화면 글자 보고 입력하기 (OCR)

```yaml
patterns:
  코드입력:
    - {do: read, region: [120, 300, 420, 340], as: 코드, pattern: "(\\d{4,6})", chars: "0123456789"}
    - {do: click_selector, selector: "#code"}
    - {do: type, text: "{var:코드}"}          # 읽은 값을 입력
    - {do: press, key: Enter}
rules:
  - name: 주문번호 보이면 처리
    when:
      - {ocr: "주문번호\\s*(PT-\\d+)", regex: true, region: [0, 100, 600, 160], as: 주문}
    then:
      - {do: type, selector: "#memo", text: "{var:주문} 처리완료"}
```

정확도 팁 (실제 측정 기준):
- **영역(region)을 값 주변으로 좁게** 잡을수록 정확합니다. 단, **글자 줄 중간을 자르면 엉뚱한 글자가 나옵니다**(예: `~ Ow 그 AT ANNN`). 관리 화면에서 영역을 그리면 잘림을 자동으로 검사해 경고하고, "영역 자동 맞춤"을 누르면 글자에 딱 맞게 고쳐 줍니다.
- 숫자·코드만 나오는 곳은 `chars: "0123456789-"` 로 허용 글자를 제한하세요.
- 한글 옆 영문 코드(예: `PT-4829`)는 한글 모드에서 `21-4829` 처럼 틀리기 쉽습니다. 정규식으로 찾다가 못 찾으면 **영어 모드로 자동 재시도**합니다. 관리 화면의 "영역 글자 읽기"도 영어 모드 결과를 함께 보여줍니다.
- 읽기에 실패하거나 형식이 안 맞으면 그 단계는 실패 처리되고, **엉뚱한 값을 입력하지 않습니다**.
- 화면 전체 OCR은 1~3초 걸립니다. 영역을 지정하면 0.3~0.5초입니다.

로컬 실행 시 설치: 우분투 `sudo apt install tesseract-ocr tesseract-ocr-kor`,
윈도우는 Tesseract(UB-Mannheim) 설치 + 한국어 데이터, 경로는 `WEBMACRO_TESSERACT` 환경변수로 지정합니다.
Docker에는 이미 포함되어 있습니다.

### 색상·좌표 고르는 법

```bash
python -m webmacro snapshot 설정.yaml -o snap.png   # 사이트 화면 저장 (서버와 같은 화면 크기)
python -m webmacro color snap.png 850 420           # → (850, 420) = #E53935
python -m webmacro check 설정.yaml                  # 설정 검사
python -m webmacro run 설정.yaml --dry-run --once   # 클릭 없이 판단만 확인
```

## 2. 안전장치

- 맞는 규칙이 없으면 **아무것도 누르지 않음**. `idle_notify` 횟수만큼 계속되면 알림
- **같은 건 두 번 처리 방지**: 클릭·입력 후, 그 규칙을 일으킨 상황(버튼·글자·읽은 값)이 사라지거나 바뀔 때까지 기다린 뒤 다음 확인 (`settle_timeout`, 기본 5초).
  느린 사이트 시험에서 대기 없이 49번 누르던 것이 정확히 3번(항목 수)으로 바뀌었습니다. 시간 안에 안 바뀌면 진행 기록에 표시하고 확인 주기만큼 쉰 뒤 다시 봅니다
- 저장 화면은 `keep_days`(7일)·`keep_shots`(500장)를 넘으면 자동 삭제, 브라우저는 쉬는 동안 `restart_browser_hours`(24시간)마다 재시작
- 관리 화면에서는 `http://`·`https://` 주소만 열 수 있음 (서버 안 파일을 `file://` 로 띄우는 것 차단. 로컬 시험용으로만 `WEBMACRO_ALLOW_FILE=1`)
- 시험 실행은 오늘 처리 건수에 들어가지 않음
- 1분 동작 수(`max_actions_per_minute`), 같은 규칙 연속 실행 수(`max_same_rule`) 초과 → 알림 후 `pause_minutes` 동안 쉼
- 단계 실패 → 스크린샷(`output/`) + 알림, 3회 연속이면 새로고침
- 브라우저가 죽거나 사이트 접속이 안 되면 → 점점 간격을 늘려 자동 재시작
- 업무 종료 후 `recheck_minutes` 를 주면 그 시간 뒤 새로고침해서 다시 감시 (0이면 프로그램 종료)

## 3. 로그인 유지

셋 중 하나:
- **자동 로그인 규칙**: `url: /login` 조건 + `type`(`{env:SITE_ID}`, `{env:SITE_PW}`) 패턴 (예시 파일 참고)
- **관리 화면에서 직접 로그인**: "조작 모드"로 서버 브라우저에서 로그인 → "로그인 상태 저장" (가장 쉬움)
- **직접 로그인한 세션 복사**: 화면 있는 PC에서 `python -m webmacro login 설정.yaml` → 브라우저에서 로그인 →
  `state/session.json` 생성 → 서버의 `data/state/` 로 복사. 실행 중 세션은 10분마다 갱신 저장됩니다.

2단계 인증·캡차가 매번 뜨는 사이트는 무인 로그인이 안 됩니다(세션 복사 방식으로 최대한 오래 유지).

## 4. 휴대폰 알림

`.env` 에 텔레그램 봇(`WEBMACRO_TELEGRAM_TOKEN`, `WEBMACRO_TELEGRAM_CHAT`) 또는
디스코드/슬랙 웹훅(`WEBMACRO_NOTIFY_URL`)을 넣으면 시작·종료·오류·일시정지를 받습니다.

## 5. 클라우드에서 24시간 실행 + 휴대폰 접속

리눅스 서버(오라클 클라우드 무료 VM, AWS Lightsail, 국내 VPS 등 우분투)에서 **명령 두 줄**이면 됩니다:

```bash
git clone -b ccr-287c0b4e-v2ibub https://github.com/UNDECT/codemaker.git && cd codemaker
sudo bash scripts/setup-server.sh
```

스크립트가 하는 일: Docker 설치 → 관리 화면 비밀번호 생성 → 서버 공인 IP로 무료 주소(`152-70-1-23.sslip.io` 형식) 생성 →
방화벽 80/443 열기 → 실행 + **HTTPS 인증서 자동 발급**(Caddy, Let's Encrypt). 끝나면 이렇게 알려줍니다:

```
 휴대폰에서 열기:  https://152-70-1-23.sslip.io
 비밀번호:         Xk3...
```

- 내 도메인이 있으면 `sudo bash scripts/setup-server.sh macro.내도메인.com` (도메인의 A 레코드를 서버 IP로)
- **오라클 클라우드**는 콘솔에서도 열어야 합니다: 네트워킹 → VCN → 보안 목록 → 수신 규칙에 TCP 80, 443 추가
- 설정·기록은 서버의 `data/` 폴더에 저장됩니다. 업데이트: `git pull && docker compose --profile https up -d --build`
- 로그: `docker compose logs -f` / 중지: `docker compose --profile https down`

직접 설정하려면 `.env.example` 을 `.env` 로 복사해 채우고 `docker compose --profile https up -d --build`.
HTTPS 없이 서버 안에서만 쓰려면 `docker compose up -d` (관리 화면은 서버의 127.0.0.1:8080, SSH 터널로 접속).

## 6. 로컬에서 실행

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m webmacro run examples/example.yaml --headed    # 브라우저 창을 보면서 실행
python -m pytest -q                                      # 테스트
```

## 7. 집 PC의 PixelTrigger와 통합 (예정)

이 저장소는 **웹사이트 전용 무인 실행기**이고, 집 PC의 PixelTrigger(`pt_assist`)는 윈도우 화면 전체를 다루는
버전입니다. 집에 가면:

1. `pt_assist` 와 `PixelTrigger_v6.0.py` 를 이 저장소의 별도 브랜치로 push
2. PixelTrigger의 색상 트리거·녹화 액션 → 이 YAML 형식으로 내보내기(또는 그 반대) 연결
3. 좌표 기준을 맞추기 위해 `viewport` 를 집 PC 브라우저 화면 크기와 동일하게 설정

## 구조

| 파일 | 역할 |
|---|---|
| `webmacro/color.py` | 스크린샷에서 색 덩어리 찾기(numpy, FHD 전체 0.1초 내외) |
| `webmacro/ocr.py` | 화면 글자 인식(Tesseract, 한글+영어) |
| `webmacro/config.py` | 설정 읽기·검증(오타·누락을 실행 전에 잡음) |
| `webmacro/engine.py` | 판단·실행 루프와 안전장치 |
| `webmacro/driver.py` | Playwright 브라우저 조작 |
| `webmacro/notify.py` | 텔레그램/웹훅 알림 |
| `webmacro/web.py` `panel.html` `login.html` | 웹 관리 화면 (현황·서버 화면·원격 로그인·규칙 편집·시작/중지) |
| `webmacro/monitor.py` | 진행 상황·오늘 건수·기록 |
| `webmacro/auth.py` | 관리 화면 로그인(쿠키 30일, 무차별 대입 차단) |
| `scripts/setup-server.sh` `deploy/Caddyfile` | 서버 한 번에 설정, 휴대폰용 HTTPS |
| `webmacro/cli.py` | 명령줄 (`panel` `check` `snapshot` `color` `login` `run`) |
