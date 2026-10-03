"""알림. 휴대폰으로 받기 위해 텔레그램 또는 웹훅(디스코드·슬랙 호환)을 지원한다.

환경변수:
  WEBMACRO_TELEGRAM_TOKEN + WEBMACRO_TELEGRAM_CHAT   텔레그램 봇
  WEBMACRO_NOTIFY_URL                                웹훅 URL (디스코드/슬랙)
둘 다 없으면 로그에만 남긴다.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

log = logging.getLogger("webmacro")


class Notifier:
    def __init__(self, prefix: str = "[webmacro]"):
        self.prefix = prefix
        self.tg_token = os.environ.get("WEBMACRO_TELEGRAM_TOKEN")
        self.tg_chat = os.environ.get("WEBMACRO_TELEGRAM_CHAT")
        self.hook = os.environ.get("WEBMACRO_NOTIFY_URL")

    def send(self, message: str):
        text = f"{self.prefix} {message}"
        log.warning("알림: %s", message)
        try:
            if self.tg_token and self.tg_chat:
                data = urllib.parse.urlencode({"chat_id": self.tg_chat, "text": text}).encode()
                urllib.request.urlopen(
                    f"https://api.telegram.org/bot{self.tg_token}/sendMessage", data=data, timeout=10)
            if self.hook:
                body = json.dumps({"content": text, "text": text}).encode()
                req = urllib.request.Request(self.hook, data=body,
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
        except Exception as e:  # 알림 실패로 매크로가 죽으면 안 된다
            log.error("알림 전송 실패: %s", e)


class MemoryNotifier(Notifier):
    """테스트용."""

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []
        self.tg_token = self.hook = None

    def send(self, message: str):
        self.messages.append(message)
