# Playwright 공식 이미지(Ubuntu 24.04 + Chromium + 의존성 포함). requirements.txt의 playwright 버전과 맞출 것.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-cjk tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY webmacro ./webmacro
ENV PYTHONPATH=/app

# 설정·세션·로그는 /data 에 둔다 (docker-compose에서 폴더 연결)
WORKDIR /data
ENTRYPOINT ["python", "-m", "webmacro"]
CMD ["run", "/data/config.yaml"]
