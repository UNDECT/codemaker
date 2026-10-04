"""자동 진행: 원하는 색을 누른 뒤 '다음단계 → 동의 → 결제수단 → 결제하기' 같은 흐름을 프로그램이 스스로 판단해 넘긴다.

한 번 부를 때마다 화면(DOM)을 읽어 할 일 하나를 고른다. 우선순위:
  1. 완료 글자(주문완료 등)가 보이면 → done
  2. 보안문자(캡차)가 보이면 → captcha (풀지 않고 사람에게 넘긴다)
  3. 결제수단(무통장입금 등)이 아직 안 골라졌으면 → 고르기
  4. 안 체크된 '동의' 체크박스 → 체크 ('전체 동의' 먼저)
  5. 비어 있는 필수 입력칸 → 입금자명이면 채우고, 모르는 칸이면 need_input
  6. 진행 버튼(결제하기 > 주문하기 > ... > 다음 > 확인) → 누르기
고른 요소에는 data-wm-target 표시를 달고, 실제 클릭·입력은 Playwright가 진짜 마우스·키보드로 한다.
"""
from __future__ import annotations

DEFAULT_BUTTONS = ["결제하기", "주문하기", "구매하기", "바로구매", "예매하기", "신청하기", "주문서작성",
                   "다음단계", "다음", "선택완료", "계속", "확인"]
DEFAULT_DONE = ["주문이완료", "주문완료", "결제완료", "구매완료", "예매완료", "신청완료", "주문이접수",
                "주문접수완료"]
CAPTCHA_TEXTS = ["보안문자", "자동입력방지", "자동입력 방지", "로봇이아닙니다"]
# 누르면 안 되는 버튼 (진행 버튼 이름이 들어가 있어도 제외)
AVOID = ["취소", "이전", "삭제", "닫기", "장바구니", "찜", "공유", "로그아웃", "카드", "간편결제", "페이"]

SCAN_JS = r"""
(o) => {
  const sq = s => (s || '').replace(/\s+/g, '');
  document.querySelectorAll('[data-wm-target]').forEach(e => e.removeAttribute('data-wm-target'));
  const vis = el => {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none'
      && parseFloat(s.opacity || '1') > 0.05;
  };
  const off = el => el.disabled || el.getAttribute('aria-disabled') === 'true';
  const labelOf = el => {
    let t = '';
    if (el.labels && el.labels.length) t = [...el.labels].map(l => l.innerText).join(' ');
    if (!t && el.closest('label')) t = el.closest('label').innerText;
    if (!t) t = el.getAttribute('aria-label') || el.title || el.placeholder || '';
    if (!t && el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t = l.innerText; }
    if (!t && el.parentElement) t = el.parentElement.innerText;
    return sq(t);
  };
  // 숨겨 둔 라디오·체크박스(예쁜 디자인용)는 보이는 라벨을 누른다
  const hit = el => vis(el) ? el : ((el.labels && [...el.labels].find(vis)) || [el.closest('label'), el.parentElement].find(vis));
  const mark = (el, kind, label, extra) => {
    el.setAttribute('data-wm-target', '1');
    return Object.assign({kind, label: (label || '').slice(0, 40)}, extra || {});
  };
  const body = sq(document.body ? document.body.innerText : '');

  for (const t of o.done) if (t && body.includes(sq(t))) return {kind: 'done', label: t};
  if (document.querySelector('iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="captcha"],iframe[title*="reCAPTCHA"]')
      || o.captcha.some(t => body.includes(sq(t)))) return {kind: 'captcha', label: '보안문자'};

  // 결제수단
  const pay = sq(o.pay);
  if (pay && !o.payDone) {
    for (const r of document.querySelectorAll('input[type=radio]')) {
      const lab = labelOf(r);
      if (!lab.includes(pay)) continue;
      if (r.checked) { o.payDone = true; break; }
      const h = hit(r); if (h && !off(r)) return mark(h, 'pay', lab);
    }
    if (!o.payDone) {
      const opts = [...document.querySelectorAll('select')].filter(s => vis(s) && !off(s));
      for (const s of opts) {
        const op = [...s.options].find(x => sq(x.text).includes(pay));
        if (op && !op.selected) return mark(s, 'select', sq(op.text), {value: op.value});
        if (op) { o.payDone = true; break; }
      }
    }
    if (!o.payDone) {
      const cands = [...document.querySelectorAll('button,a,li,label,span,div,[role=radio],[role=tab],[role=button]')]
        .filter(el => vis(el) && sq(el.innerText).includes(pay) && sq(el.innerText).length <= pay.length + 12);
      cands.sort((a, b) => sq(a.innerText).length - sq(b.innerText).length);
      if (cands.length) {
        const el = cands[0];
        const on = el.getAttribute('aria-checked') === 'true' || el.getAttribute('aria-selected') === 'true'
          || /(^|\s|-)(on|active|selected|checked)(\s|$)/.test(el.className || '');
        if (!on) return mark(el, 'pay', sq(el.innerText));
      }
    }
  }

  // 동의 체크박스 ('전체' 먼저)
  const boxes = [...document.querySelectorAll('input[type=checkbox]')]
    .filter(c => !c.checked && !off(c) && /동의|약관|확인했|확인하였|내용을확인/.test(labelOf(c)) && hit(c));
  boxes.sort((a, b) => (labelOf(b).includes('전체') ? 1 : 0) - (labelOf(a).includes('전체') ? 1 : 0));
  if (boxes.length) return mark(hit(boxes[0]), 'agree', labelOf(boxes[0]));

  // 비어 있는 필수 칸
  const req = [...document.querySelectorAll('input[required],input[aria-required=true],select[required],textarea[required]')]
    .filter(el => vis(el) && !off(el) && !['checkbox', 'radio', 'hidden', 'submit', 'button'].includes(el.type));
  for (const el of req) {
    if (el.tagName === 'SELECT') {
      if (!el.value) {
        const op = [...el.options].find(x => x.value && !x.disabled);
        let lab = labelOf(el);
        for (const x of el.options) lab = lab.replace(sq(x.text), '');
        if (op) return mark(el, 'select', (lab ? lab + ' → ' : '') + sq(op.text), {value: op.value});
      }
      continue;
    }
    if (el.value) continue;
    const lab = labelOf(el);
    if (/입금자|예금주/.test(lab) && o.depositor) return mark(el, 'fill', lab, {text: o.depositor});
    return {kind: 'need_input', label: lab.slice(0, 40) || el.name || '입력칸'};
  }

  // 진행 버튼
  const clickable = [...document.querySelectorAll('button,a,input[type=submit],input[type=button],[role=button],[onclick]')]
    .filter(el => vis(el) && !off(el));
  const textOf = el => sq(el.tagName === 'INPUT' ? el.value : (el.innerText || el.getAttribute('aria-label') || ''));
  for (const want of o.buttons) {
    const w = sq(want);
    const m = clickable.filter(el => {
      const t = textOf(el);
      return t.includes(w) && t.length <= w.length + 14 && !o.avoid.some(a => t.includes(a) && !w.includes(a));
    });
    if (!m.length) continue;
    m.sort((a, b) => textOf(a).length - textOf(b).length);
    return mark(m[0], 'click', textOf(m[0]));
  }
  return {kind: 'none'};
}
"""


def options(pay="무통장입금", depositor="", buttons=None, done=None):
    return {"pay": pay or "", "depositor": depositor or "", "payDone": False,
            "buttons": list(buttons or DEFAULT_BUTTONS), "done": list(done or DEFAULT_DONE),
            "captcha": CAPTCHA_TEXTS, "avoid": AVOID}
