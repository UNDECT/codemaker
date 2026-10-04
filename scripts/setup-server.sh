#!/usr/bin/env bash
# 클라우드 서버(우분투) 한 번에 설정: Docker 설치 → 비밀번호·주소 생성 → 방화벽 → 실행
# 사용법 (저장소 폴더에서):  sudo bash scripts/setup-server.sh  [내도메인]
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1;34m▶ %s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "sudo 로 실행하세요: sudo bash scripts/setup-server.sh"; exit 1
fi

# 1. Docker
if ! command -v docker >/dev/null 2>&1; then
  say "Docker 설치"
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || { echo "docker compose 플러그인이 필요합니다"; exit 1; }

# 1-1. 메모리가 작으면(무료 1GB 서버 등) 스왑 2GB 추가 — 크롬·글자인식이 메모리 부족으로 죽지 않게
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 3000 ] && ! swapon --show | grep -q .; then
  say "메모리 ${MEM_MB}MB → 스왑 2GB 추가"
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# 2. 접속 주소
DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  IP="$(curl -fsS https://api.ipify.org || curl -fsS https://ifconfig.me)"
  [ -n "$IP" ] || { echo "공인 IP를 알 수 없습니다. 도메인을 인자로 주세요."; exit 1; }
  DOMAIN="${IP//./-}.sslip.io"     # 무료: IP 그대로 가리키는 주소
fi

# 3. .env (비밀번호는 처음 한 번만 만든다)
touch .env && chmod 600 .env
if ! grep -q '^WEBMACRO_PANEL_PASSWORD=.\+' .env; then
  PW="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16)"
  sed -i '/^WEBMACRO_PANEL_PASSWORD=/d' .env
  echo "WEBMACRO_PANEL_PASSWORD=$PW" >> .env
fi
sed -i '/^WEBMACRO_DOMAIN=/d' .env
echo "WEBMACRO_DOMAIN=$DOMAIN" >> .env
mkdir -p data

# 4. 방화벽: 80(인증서 발급), 443(HTTPS)
say "방화벽 80/443 열기"
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q active; then
  ufw allow 80/tcp && ufw allow 443/tcp
fi
if command -v iptables >/dev/null 2>&1; then
  # 오라클 클라우드 우분투 이미지는 iptables 기본 규칙이 막고 있다
  for p in 80 443; do
    iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport "$p" -j ACCEPT
  done
  command -v netfilter-persistent >/dev/null 2>&1 && netfilter-persistent save || true
fi

# 5. 실행
say "빌드·실행 (처음엔 몇 분 걸립니다)"
docker compose --profile https up -d --build

PW_SHOW="$(grep '^WEBMACRO_PANEL_PASSWORD=' .env | cut -d= -f2-)"
cat <<EOF

────────────────────────────────────────────
 ✅ 완료

 휴대폰에서 열기:  https://$DOMAIN
 비밀번호:         $PW_SHOW

 · 처음 접속은 인증서 발급 때문에 1분쯤 걸릴 수 있습니다.
 · 안 열리면 클라우드 콘솔(오라클: VCN → 보안 목록)에서
   80, 443 포트 '수신 허용'을 추가하세요.
 · 휴대폰 브라우저 메뉴 → '홈 화면에 추가' 하면 앱처럼 쓸 수 있습니다.
 · 로그 보기:  docker compose logs -f
────────────────────────────────────────────
EOF
