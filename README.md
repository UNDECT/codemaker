# webmacro — 웹사이트 무인 매크로

웹사이트 화면에서 **색상을 찾아 클릭**하고, **상황에 따라 다른 액션패턴**(클릭·입력 순서)을 실행해
업무를 끝내는 매크로입니다. 화면 없는 브라우저(Chromium)로 동작하므로 **클라우드 서버에서 24시간**
돌릴 수 있고, 집 PC를 꺼둬도 됩니다.

```
[몇 초마다] 화면 캡처 → 규칙을 위에서부터 검사 → 처음 맞는 규칙의 액션패턴 실행
                                      └ 아무것도 안 맞으면 아무것도 누르지 않음
```

## 1. 설정 파일 (YAML)

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
| 아무 조건에 `absent: true` | **없을 때** 만족 | |

### 동작 (`do`)

| 동작 | 설명 |
|---|---|
| `click_match` | 조건에서 찾은 색 위치 클릭. `target: 이름`(as로 붙인 이름), `dx`/`dy` 보정, `double`, `button` |
| `click` | 좌표 클릭 `x`, `y` |
| `click_selector` / `click_text` | CSS 요소 / 글자를 찾아 클릭 |
| `type` | 글자 입력. `selector` 지정 시 그 칸에 입력. `{env:이름}` → 환경변수 값(비밀번호용) |
| `press` | 키 입력 (`Enter`, `Tab`, `Control+A` …) |
| `wait` | `sec` 초 대기 |
| `wait_color` | 색이 나타날 때까지 대기(`timeout`), `as: 이름`으로 위치 저장 → 다음 `click_match`에서 사용 |
| `wait_text` | 글자가 나타날 때까지 대기 |
| `scroll` `goto` `reload` | 스크롤 / 주소 이동 / 새로고침 |
| `screenshot` `notify` | 화면 저장(output/) / 휴대폰 알림 |
| `run` | 다른 패턴 실행 |
| `stop` | 업무 종료 |

### 색상·좌표 고르는 법

```bash
python -m webmacro snapshot 설정.yaml -o snap.png   # 사이트 화면 저장 (서버와 같은 화면 크기)
python -m webmacro color snap.png 850 420           # → (850, 420) = #E53935
python -m webmacro check 설정.yaml                  # 설정 검사
python -m webmacro run 설정.yaml --dry-run --once   # 클릭 없이 판단만 확인
```

## 2. 안전장치

- 맞는 규칙이 없으면 **아무것도 누르지 않음**. `idle_notify` 횟수만큼 계속되면 알림
- 1분 동작 수(`max_actions_per_minute`), 같은 규칙 연속 실행 수(`max_same_rule`) 초과 → 알림 후 `pause_minutes` 동안 쉼
- 단계 실패 → 스크린샷(`output/`) + 알림, 3회 연속이면 새로고침
- 브라우저가 죽거나 사이트 접속이 안 되면 → 점점 간격을 늘려 자동 재시작
- 업무 종료 후 `recheck_minutes` 를 주면 그 시간 뒤 새로고침해서 다시 감시 (0이면 프로그램 종료)

## 3. 로그인 유지

둘 중 하나:
- **자동 로그인 규칙**: `url: /login` 조건 + `type`(`{env:SITE_ID}`, `{env:SITE_PW}`) 패턴 (예시 파일 참고)
- **직접 로그인한 세션 복사**: 화면 있는 PC에서 `python -m webmacro login 설정.yaml` → 브라우저에서 로그인 →
  `state/session.json` 생성 → 서버의 `data/state/` 로 복사. 실행 중 세션은 10분마다 갱신 저장됩니다.

2단계 인증·캡차가 매번 뜨는 사이트는 무인 로그인이 안 됩니다(세션 복사 방식으로 최대한 오래 유지).

## 4. 휴대폰 알림

`.env` 에 텔레그램 봇(`WEBMACRO_TELEGRAM_TOKEN`, `WEBMACRO_TELEGRAM_CHAT`) 또는
디스코드/슬랙 웹훅(`WEBMACRO_NOTIFY_URL`)을 넣으면 시작·종료·오류·일시정지를 받습니다.

## 5. 클라우드에서 24시간 실행 (Docker)

리눅스 서버 아무거나(오라클 클라우드 무료 VM, AWS Lightsail, 국내 VPS 등) + Docker:

```bash
git clone https://github.com/UNDECT/codemaker.git && cd codemaker
mkdir -p data && cp examples/example.yaml data/config.yaml   # 사이트에 맞게 수정
cp .env.example .env                                         # 아이디·비번·알림 설정
docker compose up -d --build        # 백그라운드 실행 (재부팅·오류 시 자동 재시작)
docker compose logs -f              # 로그 보기
docker compose run --rm webmacro snapshot /data/config.yaml -o /data/snap.png   # 서버 화면 확인
```

설정을 바꾼 뒤에는 `docker compose restart`.

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
| `webmacro/config.py` | 설정 읽기·검증(오타·누락을 실행 전에 잡음) |
| `webmacro/engine.py` | 판단·실행 루프와 안전장치 |
| `webmacro/driver.py` | Playwright 브라우저 조작 |
| `webmacro/notify.py` | 텔레그램/웹훅 알림 |
| `webmacro/cli.py` | 명령줄 (`check` `snapshot` `color` `login` `run`) |
