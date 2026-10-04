# 휴대폰만으로 서버 만들기

- **A. AWS Lightsail (추천)** — 서울 리전(한국 IP), 휴대폰 브라우저에서 바로 접속 버튼, 월 정액 (새 계정은 일정 기간 무료인 요금제가 있음)
- **B. 오라클 클라우드 무료** — 완전 무료지만 가입·서버 생성이 자주 실패함

---

## A. AWS Lightsail (서울)

준비물: 이메일, 휴대폰, 해외결제 카드

1. **가입**: https://lightsail.aws.amazon.com 접속 → "AWS 계정 생성" → 이메일·비밀번호·카드·휴대폰 인증 → 지원 플랜은 **기본(무료)**
2. **인스턴스 만들기**: Lightsail 첫 화면 → **인스턴스 생성(Create instance)**
   - 위치: **서울 (ap-northeast-2)**
   - 플랫폼: **Linux/Unix** → 블루프린트: **OS 전용(OS Only) → Ubuntu 24.04 LTS**
   - 요금제: **메모리 2GB** 권장 (1GB도 동작 — 설치 스크립트가 스왑 자동 추가). 화면의 가격과 "무료 기간" 표시를 확인하세요
   - 이름: `macro` → **인스턴스 생성**
3. **고정 IP**: 인스턴스 → **네트워킹(Networking)** 탭 → **고정 IP 생성(Create static IP)** → 이 인스턴스에 연결
   (껐다 켜도 주소가 안 바뀜. 인스턴스에 연결돼 있으면 추가 요금 없음)
4. **포트 열기**: 같은 네트워킹 탭 → IPv4 방화벽 → **규칙 추가** → 애플리케이션 **HTTPS** (443) 저장. (HTTP 80은 기본으로 열려 있음, 없으면 같이 추가)
5. **설치**: 인스턴스 화면의 **`>_` SSH를 사용하여 연결(Connect using SSH)** 버튼 → 브라우저에 검은 창이 열림 → 붙여넣기:
   ```
   git clone https://github.com/UNDECT/codemaker.git && cd codemaker && sudo bash scripts/setup-server.sh
   ```
   10~20분 뒤 **주소와 비밀번호**가 나옵니다 — 꼭 메모
6. 아래 "휴대폰으로 접속"과 같습니다.

필요 없어지면 인스턴스와 고정 IP를 **삭제**해야 요금이 멈춥니다 (중지만 하면 계속 청구될 수 있음).

---

## B. 오라클 클라우드 무료

휴대폰 크롬으로 끝까지 할 수 있습니다. 메뉴가 영어로 보이면 괄호 안 이름을 찾으세요.
**팁:** 크롬 메뉴(⋮) → **데스크톱 사이트** 를 켜면 오라클 화면이 덜 깨집니다.

## 준비물
- 이메일, 휴대폰 번호
- 해외결제 되는 카드 (본인 확인용 소액 승인 후 취소됨. 무료 범위(Always Free)만 쓰면 청구 없음)

## 1. 가입 (10~30분)
1. https://signup.cloud.oracle.com 접속
2. 국가 **대한민국**, 이름, 이메일 → 메일로 온 링크로 인증
3. 비밀번호, 클라우드 계정 이름(아무거나 영어) 입력
4. **홈 리전**: `South Korea Central (Seoul)` 또는 `South Korea North (Chuncheon)` — **나중에 못 바꿉니다**
5. 주소·전화번호 → 카드 확인 → 완료. 계정 준비까지 몇 분~수십 분 걸릴 수 있습니다(메일 옴)

## 2. 접속 키 만들기 (Cloud Shell)
1. https://cloud.oracle.com 로그인
2. 화면 오른쪽 위 **`>_` 아이콘(Cloud Shell)** → 아래쪽에 검은 창이 열림 (처음엔 1분 정도 걸림)
3. 아래 한 줄을 붙여넣고 실행:
   ```
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 && cat ~/.ssh/id_ed25519.pub
   ```
4. 마지막에 나온 `ssh-ed25519 AAAA....` 로 시작하는 **한 줄 전체**를 길게 눌러 복사

## 3. 서버 만들기
1. 왼쪽 위 메뉴(≡) → **컴퓨트(Compute)** → **인스턴스(Instances)** → **인스턴스 생성(Create instance)**
2. 이름: `macro`
3. **이미지(Image)**: "이미지 변경(Change image)" → **Canonical Ubuntu** → 24.04 선택
4. **구성(Shape)**: "구성 변경(Change shape)" → **Ampere** → `VM.Standard.A1.Flex` → OCPU **2**, 메모리 **12GB**
   - "Always Free 적격(Always Free-eligible)" 표시가 있어야 무료
   - "용량 부족(Out of capacity)" 오류가 나면 → **AMD** → `VM.Standard.E2.1.Micro` (메모리 1GB, 설치 스크립트가 스왑을 자동으로 추가)
5. **네트워킹(Networking)**: 기본값 그대로 (새 VCN·공용 서브넷), **공용 IPv4 주소 할당** 켜짐 확인
6. **SSH 키 추가(Add SSH keys)**: **공개 키 붙여넣기(Paste public keys)** → 2단계에서 복사한 줄 붙여넣기
7. **생성(Create)** → 상태가 **실행 중(Running)** 이 되면 **공용 IP 주소(Public IP address)** 를 메모 (예: `152.70.1.23`)

## 4. 포트 열기 (휴대폰 접속용)
1. 인스턴스 화면 → **서브넷(Subnet)** 링크 클릭 → **보안 목록(Security Lists)** → `Default Security List for ...`
2. **수신 규칙 추가(Add Ingress Rules)**
   - 소스 CIDR: `0.0.0.0/0`
   - IP 프로토콜: `TCP`
   - 대상 포트 범위(Destination Port Range): `80,443`
3. **수신 규칙 추가** 버튼

## 5. 설치 (Cloud Shell에서, 10~20분)
Cloud Shell 창에 차례로 붙여넣기 (`공용IP` 는 3단계에서 메모한 주소):
```
ssh ubuntu@공용IP
```
처음 물어보면 `yes`. 접속되면:
```
git clone https://github.com/UNDECT/codemaker.git && cd codemaker && sudo bash scripts/setup-server.sh
```
끝나면 이렇게 나옵니다 — **비밀번호를 꼭 메모**하세요:
```
 휴대폰에서 열기:  https://152-70-1-23.sslip.io
 비밀번호:         Xk3...
```

## 휴대폰으로 접속 (A·B 공통)
1. 위 주소를 크롬으로 열기 (처음엔 인증서 발급 때문에 1분쯤 걸릴 수 있음)
2. 비밀번호 입력 → **현황 → ▶ 시작** → 연습용 결재함 5건이 처리되는지 확인
3. 크롬 메뉴(⋮) → **홈 화면에 추가** 하면 앱처럼 열림

이제 휴대폰을 꺼도, Cloud Shell을 닫아도 매크로는 서버에서 계속 돕니다.

## 문제가 생기면
| 증상 | 해결 |
|---|---|
| 서버 생성 시 "Out of capacity" | 잠시 뒤 다시 시도, 다른 가용성 도메인 선택, 또는 E2.1.Micro 사용 |
| 주소가 안 열림 | 4단계 수신 규칙(80, 443) 확인. 서버에서 `cd ~/codemaker && sudo docker compose --profile https logs caddy` |
| Cloud Shell이 끊김 | 다시 `ssh ubuntu@공용IP`. 매크로는 영향 없음 |
| 비밀번호를 잊음 | 서버에서 `cat ~/codemaker/.env` |
| 업데이트 | 서버에서 `cd ~/codemaker && git pull && sudo docker compose --profile https up -d --build` |

참고: 오라클은 오래 거의 놀고 있는 무료 서버를 회수할 수 있다고 안내합니다. 매크로가 주기적으로 돌면 보통 괜찮지만,
오랫동안 안 쓸 거라면 가끔 접속해서 상태를 확인하세요.
