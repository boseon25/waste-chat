"""시연용 HTML 한 장 (2026.11.20) — 여러 사례 결과 파일을 받아 사례마다
  분류 에이전트가 준 정보 → 결론 → 추천 업체 → 해야 할 일 → 확인할 것 → 판단 근거(접힘)
을 위에서 아래로 보여 준다. 판단을 새로 하지 않고 저장된 결과(law_review·routes)만 읽는다.

  python3 -m law_agent.output.demo_html <출력.html> <결과1.json> [결과2.json …] [--input 분류에이전트_입력.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from law_agent.output import plain
from law_agent.output.cards_html import _e, body, page

ROLE_KO = {"recycler": "재활용", "preprocessor": "중간 가공", "transporter": "운반", "treatment": "처분",
           "end_user": "구매처"}
DESIG_KO = {"non_designated": "지정폐기물 아님", "designated": "지정폐기물", "suspect": "지정폐기물 의심"}

# 업체를 뺀 이유 → 쉬운 말 + 법 근거 (근거가 법이 아닌 것은 '서비스 기준')
REASON = {
    "자가처리만 신고": ("남의 폐기물을 위탁받지 않는 업체", "폐기물관리법 제18조 제1항 (허가·신고한 처리업자에게만 위탁)"),
    "지정/일반 범위 밖": ("지정폐기물·일반폐기물 허가 구분이 다름", "폐기물관리법 제25조 (허가 범위)"),
    "처리 방법 다름": ("허가받은 처리방법이 다름", "폐기물관리법 제25조 (허가 범위)"),
    "재활용 유형 다름": ("허가받은 재활용 유형이 다름", "폐기물관리법 제25조 (허가 범위) · 시행규칙 별표 4의3"),
    "혼합물 일부만 허가": ("섞인 성분 중 일부만 허가", "폐기물관리법 제25조 (허가 범위)"),
    "영업 중단": ("영업 중이 아님", "폐기물관리법 제25조 (허가 유지)"),
    "건설폐기물만": ("건설폐기물만 운반하는 업체", "건설폐기물법 (별도 허가)"),
    "대상 폐기물 다름": ("허가받은 운반 대상 폐기물이 아님", "폐기물관리법 제25조 (허가 범위)"),
    "혼합물 일부만 대상": ("섞인 성분 중 일부만 운반 가능", "폐기물관리법 제25조 (허가 범위)"),
    "업체 거절 (회신)": ("업체가 거절함", "업체 회신"),
}

EXTRA_CSS = """
.funnel{margin:6px 0 10px;font-size:13px}
.funnel .row{display:flex;gap:10px;padding:4px 0;border-bottom:1px dashed var(--line)}
.funnel .row b{min-width:62px;text-align:right}
.funnel .row .why{flex:1}.funnel .row .law{color:var(--sub);font-size:12px}
.funnel .keep b{color:var(--ok)}
.cks{margin:4px 0 6px;font-size:13px}.ck{display:flex;gap:8px;padding:3px 0;align-items:baseline}
.ck .y{color:var(--ok);font-weight:700}.ck .s{color:var(--sub)}.ck .w{color:var(--bad);font-weight:700}.ck .q{color:var(--warn);font-weight:700}
.ck .law{margin-left:auto;color:var(--sub);font-size:11px;text-align:right}
.top{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 18px}
.top a{font-size:14px;text-decoration:none;color:var(--text);background:#fff;border:1px solid var(--line);border-radius:999px;padding:6px 14px}
.notice{font-size:12px;color:var(--warn);background:var(--warn-bg);border-radius:8px;padding:8px 12px;margin-bottom:18px}
.case{margin:0 0 42px}
.case>h2{font-size:21px;margin:0 0 4px}
.case>.sub{color:var(--sub);font-size:13px;margin:0 0 12px}
.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px 14px;font-size:14px}
.facts div span{display:block;font-size:12px;color:var(--sub)}
table.p{width:100%;border-collapse:collapse;font-size:14px}
table.p th{font-size:12px;color:var(--sub);font-weight:500;text-align:left;padding:6px 6px;border-bottom:1px solid var(--line)}
table.p td{padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
table.p td.r{text-align:right;white-space:nowrap}
.rank{display:inline-flex;width:22px;height:22px;border-radius:50%;background:var(--ok-bg);color:var(--ok);font-size:12px;
font-weight:700;align-items:center;justify-content:center}
.small{font-size:12px;color:var(--sub)}
"""


def _avg(xs) -> float | None:
    xs = [float(x) for x in xs or [] if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else None


def facts_html(case: dict) -> str:
    cls = case.get("classification") or {}
    q = case.get("quantity") or {}
    avg = _avg(q.get("monthly_ton"))
    reg = case.get("region") or {}
    cont = case.get("contamination") or {}
    rows = [("폐기물", case.get("name")),
            ("분류번호", " ".join(x for x in (cls.get("selected_code") or cls.get("reported_code"), cls.get("code_label")) if x)),
            ("지정폐기물", DESIG_KO.get(case.get("designated_waste_status"), case.get("designated_waste_status"))),
            ("월 평균 발생량", f"{avg * 1000:.0f} kg" if avg is not None else None),
            ("성상", case.get("material_form")),
            ("오염", cont.get("level")),
            ("발생 공정", case.get("process_description")),
            ("지역", " ".join(x for x in (reg.get("sido"), reg.get("sigungu")) if x))]
    cells = "".join(f"<div><span>{_e(k)}</span>{_e(v)}</div>" for k, v in rows if v not in (None, ""))
    return f'<div class="card"><h2>분류 에이전트가 준 정보</h2><div class="facts">{cells}</div></div>'


def _dist(c: dict) -> str:
    from law_agent.core.partners.evidence import distance_text
    return c.get("distance_text") or distance_text(c.get("location") or None)


def checks(c: dict, role: str, designated: bool) -> list[tuple[str, str, str]]:
    """매칭 결과에 evidence·cautions 가 있으면 그대로 (2026.11.20 이후 결과). 없으면 아래 옛 방식으로 만든다"""
    if c.get("evidence") or c.get("cautions"):
        out = [("✓", f'{e["item"]}: {e["value"]}', e.get("law") or "") for e in c.get("evidence") or []]
        out += [("!", x, "확인 필요") for x in c.get("cautions") or []]
        out.append(("?", "허가증(신고증명서) 사본", "시행규칙 제16조의13 제1호 가목 — 업체에서 받기"))
        out.append(("?", "수탁처리능력 확인서 (제5호서식)", "폐기물관리법 제17조제1항제3호 — 업체 회신"))
        return out
    return _checks_old(c, role, designated)


def _checks_old(c: dict, role: str, designated: bool) -> list[tuple[str, str, str]]:
    """순위에 든 업체 하나의 확인표 [(✓ 또는 ?, 항목, 근거·출처)]. ✓ = 업체 자료로 확인됨, ? = 회신·서류로 확인할 것"""
    out = []
    src = f'업체 자료 {c.get("data_date") or ""}년'.strip()
    if role == "transporter":
        out.append(("✓", "영업 중인 수집·운반업체", f"폐기물관리법 제25조 · {src}"))
        out.append(("✓", f'이 폐기물을 운반할 수 있는 허가 ({c.get("registration_type") or "대상군 확인"})', f"폐기물관리법 제25조 · {src}"))
        if designated:
            out.append(("✓", "지정폐기물 운반 허가", f"폐기물관리법 제25조 · {src}"))
    else:
        out.append(("✓", f'남의 폐기물을 위탁받는 {c.get("permit_type") or "허가·신고"} 업체', f"폐기물관리법 제18조 제1항 · {src}"))
        out.append(("✓", f'허가 폐기물 분류 {c.get("waste_group") or ""}'.strip(), f"폐기물관리법 제25조 · {src}"))
        if designated:
            out.append(("✓", "지정폐기물 처리 허가", f"폐기물관리법 제25조 (D-5 판단) · {src}"))
        if c.get("treatment_codes"):
            out.append(("✓", f'처리방법 코드 {", ".join(c["treatment_codes"])}', f"폐기물관리법 제25조 · {src}"))
        if c.get("r_codes"):
            out.append(("✓", f'재활용 유형 {", ".join(c["r_codes"])}', f"시행규칙 별표 4의3 (D-1 판단) · {src}"))
        if c.get("registration_type"):
            out.append(("✓", f'등록 형태: {c["registration_type"]}', "서비스 범위 (중간재활용업)"))
    if c.get("scope_text"):
        out.append(("✓", f'영업 대상 (업체 자료 원문): {c["scope_text"][:120]}', "업체 자료"))
    out.append(("?", "허가증(신고증명서) 사본", "시행규칙 제16조의13 제1호 가목 — 업체에서 받기"))
    out.append(("?", "수탁처리능력 확인서 (제5호서식)", "폐기물관리법 제17조제1항제3호 — 업체 회신"))
    if role != "transporter":
        out.append(("?", "남은 처리 용량·반입 기준·처리 단가", "업체 회신"))
    return out


def _checks_html(c: dict, role: str, designated: bool) -> str:
    rows = "".join(f'<div class="ck"><span class="{"y" if m == "✓" else "q" if m == "?" else "w" if m == "!" else "s"}">{m}</span>{_e(t)}'
                   f'<span class="law">{_e(b)}</span></div>' for m, t, b in checks(c, role, designated))
    return (f'<details><summary><span class="arrow">▶</span><span class="k">확인표 보기</span></summary>'
            f'<div class="cks">{rows}</div></details>')


def partners_html(res: dict, top: int = 5) -> str:
    rows = []
    designated = any((r.get("waste_status") or "") == "designated" for r in res.get("routes") or []) or \
        str((res.get("preliminary") or {}).get("waste_status") or "").startswith("designated")
    for r in res.get("routes") or []:
        for s in ((r.get("partners") or {}).get("steps") or []):
            cands = s.get("candidates") or []
            if not cands:
                continue
            trs = "".join(
                f'<tr><td><span class="rank">{i}</span></td><td><b>{_e(c.get("name"))}</b>'
                f'<div class="small">{_e(c.get("sigungu"))} · {_e(c.get("registration_type") or c.get("permit_type") or "")}</div>'
                + (f'<div class="small" style="color:var(--bad)">! 확인 필요 {len(c.get("cautions") or [])}건</div>' if c.get("cautions") else '')
                + f'{_checks_html(c, s.get("role"), designated)}</td>'
                f'<td>{_e(", ".join(c.get("treatment_codes") or []) or "-")}</td><td class="r">{_e(_dist(c))}</td></tr>'
                for i, c in enumerate(cands[:top], 1))
            more = s.get("total_found") or len(cands)
            rej = s.get("rejected_summary") or {}
            fun = ""
            if rej:
                total = more + sum(rej.values())
                fun = (f'<div class="funnel"><div class="row"><b>{total}곳</b><span class="why">같은 폐기물 분류를 다루는 업체'
                       + (' (서비스 범위: 중간재활용업 등록 업체)' if s.get("role") == "preprocessor" else "") + '</span></div>'
                       + "".join(f'<div class="row"><b>−{n}곳</b><span class="why">{_e(REASON.get(k, (k, ""))[0])}'
                                 f'<div class="law">근거: {_e(REASON.get(k, (k, "서비스 기준"))[1] or "서비스 기준")}</div></span></div>'
                                 for k, n in sorted(rej.items(), key=lambda x: -x[1]))
                       + f'<div class="row keep"><b>{more}곳</b><span class="why">법적으로 맡길 수 있는 업체 → 도로 거리 순 상위 {min(top, len(cands))}곳'
                       + '<div class="law">순위 기준: 도로 거리 (서비스 기준, 법 아님)</div></span></div></div>')
            rows.append(f'<h3 style="font-size:14px;margin:14px 0 6px">{_e(ROLE_KO.get(s.get("role"), s.get("role")))} 업체'
                        f' <span class="small">(조건에 맞는 {more}곳 중 가까운 순)</span></h3>'
                        + fun + f'<table class="p"><tr><th></th><th>업체</th><th>처리방법 코드</th><th style="text-align:right">거리</th></tr>{trs}</table>')
        if rows:
            break                                        # 첫 경로만 (지금 범위: R-10 한 경로)
    if not rows:
        return ""
    return ('<div class="card"><h2>추천 업체</h2>' + "".join(rows)
            + '<p class="small" style="margin-top:10px">허가 범위(분류번호·처리방법·재활용 유형)가 맞는 업체만 남기고 '
              '도로 거리 순으로 정렬. 처리 단가·반입 기준은 업체 회신으로 확인합니다.</p></div>')


def section(res: dict, case: dict | None) -> str:
    c = plain.cards_from_result(res)
    cid = res.get("case_id") or ""
    name = (case or {}).get("name") or cid
    head = f'<section class="case" id="c-{_e(cid)}"><h2>{_e(name)}</h2><p class="sub">사례 {_e(cid)}</p>'
    return head + (facts_html(case) if case else "") + body(c, partners_html(res)) + "</section>"


def build(results: list[dict], inputs: dict[str, dict], notice: str = "") -> str:
    nav = "".join(f'<a href="#c-{_e(r.get("case_id"))}">{_e((inputs.get(r.get("case_id")) or {}).get("name") or r.get("case_id"))}</a>'
                  for r in results)
    inner = ('<h1 style="font-size:24px;color:var(--text);font-weight:700">law_agent 시연 — 법률 판단 · 업체 추천</h1>'
             + (f'<div class="notice">{_e(notice)}</div>' if notice else "")
             + f'<div class="top">{nav}</div>'
             + "".join(section(r, inputs.get(r.get("case_id"))) for r in results))
    return page(inner, "law_agent 시연", EXTRA_CSS)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    inp = argv[argv.index("--input") + 1] if "--input" in argv else None
    if inp in args:
        args.remove(inp)
    if len(args) < 2:
        print(__doc__)
        return 2
    out, files = args[0], args[1:]
    inputs, notice = {}, ""
    if inp:
        d = json.loads(Path(inp).read_text(encoding="utf-8"))
        for c in d.get("cases") or [d]:
            inputs[c.get("case_id")] = c
        notice = d.get("sample_notice") or ""
    results = [json.loads(Path(f).read_text(encoding="utf-8")) for f in files]
    Path(out).write_text(build(results, inputs, notice), encoding="utf-8")
    print(f"시연 화면: {out} ({len(results)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
