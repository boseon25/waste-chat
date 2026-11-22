"""사용자용 법률 카드 → HTML 한 장 (2026.11.19).

처음 화면은 간단하게: 결론 · 해야 할 일(제목·언제) · 확인할 것.
세부사항은 눌러서 펼친다 (<details>): 할 일 설명·서류·근거 / 판단 근거 목록 → 판단 하나 → 요약·근거 조문·원문.
자바스크립트 없이 HTML·CSS 만 쓴다 (파일 하나, 브라우저로 바로 열림, waste-chat 에 그대로 옮길 수 있음).
"""
from __future__ import annotations

import html

STATUS_CLASS = {"가능": "ok", "조건부 가능": "warn", "불가": "bad", "확인 필요": "muted"}
LABEL_CLASS = {"확인됨": "ok", "조건부": "warn", "담당자 확인 필요": "bad", "정보 필요": "info", "기준 찾음": "info"}
LABEL_ICON = {"확인됨": "✓", "조건부": "!", "담당자 확인 필요": "?", "정보 필요": "…", "기준 찾음": "≡"}

CSS = """
:root{--ok:#1d7a4f;--ok-bg:#e7f5ee;--warn:#9a5b00;--warn-bg:#fdf3e1;--bad:#b4232a;--bad-bg:#fcebec;
--info:#1f5fa8;--info-bg:#e8f1fb;--muted:#5f5e5a;--muted-bg:#f1efe8;--line:#e4e2dc;--text:#1f1f1d;--sub:#6b6a66}
*{box-sizing:border-box}body{margin:0;background:#f7f6f3;color:var(--text);
font-family:-apple-system,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;line-height:1.6}
.wrap{max-width:760px;margin:0 auto;padding:28px 18px 60px}
h1{font-size:15px;font-weight:500;color:var(--sub);margin:0 0 14px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:14px}
.concl{border-left:5px solid var(--c);}
.badge{display:inline-block;font-size:13px;font-weight:600;padding:2px 10px;border-radius:999px;background:var(--bg);color:var(--c)}
.ok{--c:var(--ok);--bg:var(--ok-bg)}.warn{--c:var(--warn);--bg:var(--warn-bg)}.bad{--c:var(--bad);--bg:var(--bad-bg)}
.info{--c:var(--info);--bg:var(--info-bg)}.muted{--c:var(--muted);--bg:var(--muted-bg)}
.concl .main{font-size:19px;font-weight:600;margin:10px 0 6px}
.concl ul{margin:6px 0 0;padding-left:18px;color:#3d3d3a}.concl li{margin:2px 0}
h2{font-size:15px;font-weight:600;margin:0 0 10px}
details{border-top:1px solid var(--line)}details:first-of-type{border-top:none}
summary{cursor:pointer;list-style:none;padding:11px 2px;display:flex;align-items:center;gap:10px}
summary::-webkit-details-marker{display:none}
summary .arrow{color:var(--sub);font-size:12px;width:12px;transition:transform .15s}
details[open]>summary .arrow{transform:rotate(90deg)}
.num{width:22px;height:22px;border-radius:50%;background:var(--muted-bg);color:var(--muted);font-size:12px;
display:inline-flex;align-items:center;justify-content:center;flex:none}
.ttl{font-weight:500}.when{color:var(--sub);font-size:13px;margin-left:auto;text-align:right}
.body{padding:2px 4px 14px 34px;font-size:14px;color:#3d3d3a}
.body p{margin:4px 0}.k{color:var(--sub);font-size:12px;margin-right:6px}
.small{font-size:12px;color:var(--sub)}
.std{border-top:1px solid var(--line);padding:8px 0}.std:first-of-type{border-top:none}
.stdh{display:flex;justify-content:space-between;gap:8px;margin-bottom:4px}
.stdl{background:#f6f8fb;border-left:3px solid var(--info);padding:6px 10px;margin:4px 0;font-size:14px;line-height:1.55}
.stdl .draft,.plain .draft{margin-left:8px;font-size:11px;color:#9a5b00;background:#fdf3e1;border-radius:4px;padding:1px 5px;white-space:nowrap}
.plain{background:#eef6f1;border-left:3px solid #1d9e75;padding:7px 10px;margin:4px 0 6px;font-size:14px;line-height:1.55}
.plain .draft{margin-left:8px;font-size:11px;color:#9a5b00;background:#fdf3e1;border-radius:4px;padding:1px 5px;white-space:nowrap}
.quote{background:#faf9f6;border-left:3px solid var(--line);padding:6px 10px;margin:4px 0 8px;font-size:13px;color:#4a4946}
.ref{font-size:13px;margin-top:8px}
.ico{width:22px;height:22px;border-radius:50%;background:var(--bg);color:var(--c);font-size:12px;font-weight:700;
display:inline-flex;align-items:center;justify-content:center;flex:none}
.check{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-top:1px solid var(--line);font-size:14px}
.check:first-of-type{border-top:none}
.more>summary{font-weight:600;font-size:15px;padding:4px 2px}
.note{font-size:12px;color:var(--sub);margin-top:18px}
.full{white-space:pre-wrap;font-size:13px;color:#4a4946;background:#faf9f6;padding:8px 10px;border-radius:6px}
.body details{border-top:none}.body summary{padding:2px 0}
"""


def _e(s) -> str:
    return html.escape(str(s or ""))


def _basis(blist: list[dict], limit: int = 3) -> str:
    """근거 조문 이름 + (쉬운 요약이 있으면 요약 → 인용·원문은 펼쳐서 / 없으면 인용 → 원문은 펼쳐서)"""
    out = []
    for b in (blist or [])[:limit]:
        out.append(f'<div class="ref"><span class="k">{_e(b.get("role") or "근거")}</span>{_e(b.get("where"))}</div>')
        q = " ".join((b.get("quote") or "").split())
        src = b.get("source") or ""
        pl = b.get("plain")
        full = src if src and " ".join(src.split()) != q else ""
        if pl and pl.get("summary"):
            tag = "" if pl.get("reviewed") else '<span class="draft">AI 요약 · 검수 전</span>'
            out.append(f'<div class="plain">{_e(pl["summary"])}{tag}</div>')
            inner = (f'<div class="quote">{_e(q)}</div>' if q else "") + (f'<div class="full">{_e(full)}</div>' if full else "")
            if inner:
                out.append(f'<details><summary><span class="arrow">▶</span><span class="k">원문 보기</span></summary>{inner}</details>')
            continue
        if q:
            out.append(f'<div class="quote">{_e(q)}</div>')
        if full:
            out.append(f'<details><summary><span class="arrow">▶</span><span class="k">원문 보기</span></summary>'
                       f'<div class="full">{_e(full)}</div></details>')
    return "".join(out)


def page(inner: str, title: str = "", extra_css: str = "") -> str:
    return (f'<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(title) or "법률 확인 결과"}</title><style>{CSS}{extra_css}</style></head>'
            f'<body><div class="wrap">{inner}</div></body></html>')


def render(c: dict, title: str = "") -> str:
    return page((f"<h1>{_e(title)}</h1>" if title else "") + body(c), "법률 확인 결과" + (f" · {title}" if title else ""))


def body(c: dict, partners_html: str = "") -> str:
    """결론 → (추천 업체) → 해야 할 일 → 확인할 것 → 판단 근거(접힘)"""
    k = c.get("conclusion") or {}
    st = k.get("status", "")
    parts = []

    # ① 결론
    notes = "".join(f"<li>{_e(n)}</li>" for n in k.get("notes") or [])
    parts.append(f'<div class="card concl {STATUS_CLASS.get(st, "muted")}">'
                 f'<span class="badge">{_e(st)}</span>'
                 f'<div class="main">{_e(k.get("text"))}</div>'
                 f'{"<ul>" + notes + "</ul>" if notes else ""}</div>')

    if partners_html:
        parts.append(partners_html)

    # ② 해야 할 일 — 제목·언제만 보이고 눌러서 설명·서류·근거
    todos = c.get("todos") or []
    if todos:
        rows = []
        for i, t in enumerate(todos, 1):
            body = f'<p>{_e(t.get("what"))}</p>'
            if t.get("doc"):
                body += f'<p><span class="k">서류</span>{_e(t["doc"])}</p>'
            body += f'<p><span class="k">근거</span>{_e(t.get("basis"))}</p>'
            if t.get("basis_quotes"):
                body += _basis(t["basis_quotes"], 2)
            rows.append(f'<details><summary><span class="arrow">▶</span><span class="num">{i}</span>'
                        f'<span class="ttl">{_e(t.get("title"))}</span>'
                        f'<span class="when">{_e(t.get("when"))}</span></summary>'
                        f'<div class="body">{body}</div></details>')
        parts.append(f'<div class="card"><h2>해야 할 일</h2>{"".join(rows)}</div>')

    # ②-2 맡길 업체가 지켜야 할 기준 — 기준마다 한 줄, 원문은 펼쳐서
    std = c.get("partner_standards") or []
    if std:
        rows = []
        for x in std:
            lines = ""
            for ln in x.get("lines") or []:
                tag = "" if not ln.get("plain") or ln.get("reviewed") else '<span class="draft">AI 요약 · 검수 전</span>'
                lines += (f'<div class="stdl">{_e(ln.get("text"))}{tag}<div class="small">{_e(ln.get("where"))}</div>'
                          f'<details><summary><span class="arrow">▶</span><span class="k">원문 보기</span></summary>'
                          f'<div class="full">{_e(ln.get("source"))}</div></details></div>')
            rows.append(f'<div class="std"><div class="stdh"><b>{_e(x.get("title"))}</b>'
                        f'<span class="when">{_e(x.get("who"))} · {_e(x.get("when"))}</span></div>{lines}</div>')
        parts.append(f'<div class="card"><h2>맡길 업체가 지켜야 할 기준</h2>'
                     f'<p class="small">업체 의무입니다. 업체를 고를 때 지킬 수 있는 곳인지 확인하세요.</p>{"".join(rows)}</div>')

    # ③ 확인할 것
    checks = c.get("checks") or []
    if checks:
        rows = []
        for x in checks:
            cls = "info" if x.get("kind") in ("알려 주세요", "자료를 내 주세요") else "bad"
            icon = "…" if cls == "info" else "?"
            rows.append(f'<div class="check {cls}"><span class="ico">{icon}</span>'
                        f'<div><b>{_e(x.get("kind"))}</b> · {_e(x.get("text"))}</div></div>')
        parts.append(f'<div class="card"><h2>확인할 것</h2>{"".join(rows)}</div>')

    # ④ 판단 근거 — 통째로 접어 두고, 펼치면 판단마다 또 접힘
    det = c.get("details") or []
    if det:
        rows = []
        for d in det:
            lab = d.get("label", "")
            cls = LABEL_CLASS.get(lab, "muted")
            body = f'<p>{_e(d.get("summary"))}</p>'
            for x in d.get("conditions") or []:
                body += f'<p><span class="k">조건</span>{_e(x)}</p>'
            if d.get("detail"):
                short = d.get("detail_short") or d["detail"]
                body += f'<p><span class="k">내용</span>{_e(short)}</p>'
                if len(d["detail"]) > len(short):
                    body += (f'<details><summary><span class="arrow">▶</span><span class="k">내용 전체 보기</span>'
                             f'</summary><div class="full">{_e(d["detail"])}</div></details>')
            body += _basis(d.get("basis"))
            rows.append(f'<details><summary><span class="arrow">▶</span>'
                        f'<span class="ico {cls}">{LABEL_ICON.get(lab, "·")}</span>'
                        f'<span class="ttl">{_e(d.get("title"))}</span>'
                        f'<span class="when"><span class="badge {cls}">{_e(lab)}</span></span></summary>'
                        f'<div class="body">{body}</div></details>')
        parts.append(f'<div class="card"><details class="more"><summary><span class="arrow">▶</span>'
                     f'판단 근거 보기 ({len(det)}건)</summary>{"".join(rows)}</details></div>')

    if not c.get("reviewed"):
        parts.append('<div class="note">안내 문장은 초안입니다. 법령 원문이 우선합니다.</div>')
    return "".join(parts)
