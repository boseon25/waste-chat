"""필요한 부분만 고르기 (조문 항·호·목 / 별표 호·목 / 행정규칙 장·절) + 감시 대상 판단"""
import re

from .chunker import HANGUL_MARK, h, text_of


def content(node):
    """'…내용' 칸만 순서대로 모은다 (번호·키·일자 제외)"""
    parts = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k.endswith("내용"):
                parts.append(text_of(v))
            elif isinstance(v, (dict, list)):
                parts.append(content(v))
    elif isinstance(node, list):
        parts += [content(x) for x in node]
    return "\n".join(p for p in parts if p and p.strip())


MARKS = [(0, re.compile(r"^(\d+)\.(?!\d)")), (1, re.compile(rf"^([{HANGUL_MARK}])\.")),
         (2, re.compile(r"^(\d+)\)")), (3, re.compile(rf"^([{HANGUL_MARK}])\)"))]


def annex_part(text, path_str):
    # "3.다.3)" / "3.라" / "4.나.6)" 모두 허용: 표지 뒤 구분자가 없으면 "."으로 본다
    target = [a + (b or ".") for a, b in re.findall(rf"(\d+|[{HANGUL_MARK}])([.)])?", path_str)]
    stack, keep, top_ind = [], [], None
    for line in text.splitlines():
        s_ = line.strip()
        ind = len(line) - len(line.lstrip())
        for lvl, rx in MARKS:
            m = rx.match(s_)
            if m:
                # 줄바꿈으로 생긴 가짜 표지 거르기: 호(1.)는 들여쓰기 4칸 이하만, 목(가.) 이하는 호보다 깊게 들여쓴 것만
                if lvl == 0 and ind > 4:
                    continue
                if lvl >= 1 and top_ind is not None and ind <= top_ind:
                    continue
                if lvl == 0:
                    top_ind = ind
                stack = stack[:lvl] + [None] * max(0, lvl - len(stack)) + [m.group(0)]
                stack = stack[:lvl + 1]
                break
        if stack[:len(target)] == target:
            keep.append(line)
    return "\n".join(keep) if keep else None


def select_part(chunks, spec):
    """spec 하나에 해당하는 텍스트를 돌려준다: (title, text) 또는 (None, 이유)"""
    if spec.startswith("§"):
        hit = [c for c in chunks if c["type"] == "doc_part" and c["key"] == spec]
        return (hit[0]["title"], hit[0]["text"]) if hit else (None, f"{spec} 없음")
    if spec == "*":
        w = [c for c in chunks if c["type"] == "whole"]
        return (w[0]["title"], w[0]["text"]) if w else (None, "본문 전체 청크 없음")
    if spec.startswith("B"):
        rest, kw, path = spec[1:], None, None
        if "@" in rest:
            rest, kw = rest.split("@", 1)
        if ":" in rest:
            rest, path = rest.split(":", 1)
        anx = [c for c in chunks if c["type"] == "annex"]
        hit = [c for c in anx if rest[1:] in str(c["title"])] if rest.startswith("*") else [c for c in anx if c["key"] == rest]
        if not hit:
            return None, f"별표 {rest} 없음"
        c = hit[0]
        text = c["text"]
        if kw:
            lines = text.splitlines()
            idx = next((i for i, l in enumerate(lines) if kw.replace(" ", "") in l.replace(" ", "")), None)
            if idx is None:
                return c["title"], text + "\n[주의] 키워드를 찾지 못해 전체를 담음"
            text = "\n".join(lines[idx:])
        if path:
            part = annex_part(text, path)
            if part is None:
                return None, f"별표 {rest}에서 {path} 못 찾음"
            text = part
        return c["title"], text
    art = spec.split("/")[0]
    if "/" in spec:
        hit = [c for c in chunks if c["type"] == "article_part" and c["key"] == spec]
        if hit:
            return hit[0]["title"], hit[0]["text"]
        whole = [c for c in chunks if c["type"] == "article" and c["key"] == art]
        if whole:
            return whole[0]["title"], whole[0]["text"] + "\n[주의] " + spec + " 를 못 찾아 조문 전체를 담음"
        return None, f"제{art}조 없음"
    hit = [c for c in chunks if c["type"] == "article" and c["key"] == art]
    return (hit[0]["title"], hit[0]["text"]) if hit else (None, f"제{art}조 없음")


def parts_of(t, chunks):
    res = {}
    for spec in t.get("parts", []):
        title, text = select_part(chunks, spec)
        if title is None:
            res[spec] = {"found": False, "reason": text}
        else:
            res[spec] = {"found": True, "title": title, "text": text, "hash": h(text)}
    return res


def watched(t, c):
    if c["type"] == "whole":
        return not t["articles"] and not t["annexes"]  # 감시 범위를 따로 안 정한 행정규칙만 전체 감시
    if c["type"] == "article":
        return c["key"] in t["articles"]
    # 주의: 예전 그대로 annex_part·doc_part도 여기서 판단한다 (annexes=["*"]면 감시에 들어감).
    #       바꾸면 기존 registry.json의 감시 키와 어긋난다.
    for a in t["annexes"]:
        if a == "*" or c["key"] == a or (a.startswith("*") and a[1:] in str(c["title"])):
            return True
    return False
