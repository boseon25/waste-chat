#!/usr/bin/env python3
"""
법령 감시 스크립트 (국가법령정보 OPEN API)

사용법
  export LAW_OC=발급받은_인증키          # Windows: set LAW_OC=...
  python law_watch.py baseline           # 처음 1회: 대상 원문 저장 + 기준선(registry.json) 생성
  python law_watch.py check              # 이후 주 1회: 기준선과 비교해 보고서 생성
  python law_watch.py inspect WMA_DEC    # 저장된 원문 JSON의 키 구조 출력 (파서 점검용)
  python law_watch.py extract            # 저장된 원문에서 감시 대상만 뽑아 data/law/watched/에 저장
  python law_watch.py extract WMA_RULE "B5:4.나.6)"   # 특정 법령의 특정 부분만 (parts에 적힌 표기 그대로)
  python law_watch.py extract --pdf      # 감시 별표의 PDF 원본도 함께 다운로드
  python law_watch.py titles WMA_RULE    # 감시 조문·별표의 지금 제목을 targets.py 형식으로 출력
  python law_watch.py refs [CSV·폴더 …]  # 판정 표의 근거 키(LAW:KEY@날짜#hash) 점검 (기본: data/tables)

원칙
  - 판정 표(CSV)는 절대 수정하지 않는다. 원문 저장 + 변경 보고서 + 발췌까지만.
  - 원문은 시행일별로 저장하고 덮어쓰지 않는다.
  - 호출 사이 1.2초 간격. 인증키는 LAW_OC로만, 보고서·오류에는 OC=*** 로 가린다.
  - 번호 이동(예: 제16조의12 → 제16조의13)은 보고만 한다. targets.py·판정 표는 사람이 고친다.

대상 목록은 law_core/targets.py, 저장 위치는 law_core/store.py 참고.
"""
import csv, json, re, sys
from datetime import date
from pathlib import Path

# 예전 이름 그대로 내보낸다 (다른 코드가 law_watch에서 가져다 쓸 수 있음)
from law_core.targets import TARGETS, BY_ID, pick, ids, title_of
from law_core.api import (BASE, SLEEP, NAME_KEYS, mask, oc, get_json, search, find_item, pick_item,
                          meta_of, fetch_detail, download)
from law_core.chunker import (NORMALIZE, normalize, h, hash_text, walk, as_list, text_of, CIRCLED, HANGUL_MARK,
                              norm_no, art_key, annex_key, article_text, article_counts, ROMAN, ROMAN_SEQ, SKIP_KEYS,
                              RX_ARTICLE, RX_ROMAN, RX_NUM, admrul_body, split_at, norm_roman, split_seq,
                              admrul_chunks, chunks_of, move_hash)
from law_core.parts import content, MARKS, annex_part, select_part, parts_of, watched
from law_core.store import (ROOT, save, save_raw, save_chunks, registry_path, load_registry, save_registry,
                            read_chunks, chunks_at, latest_chunks, write_report)


def snapshot(t, meta, chunks):
    return {"name": t["name"], "grade": t["grade"], **{k: v for k, v in meta.items() if k != "detail_link"},
            "checked": date.today().isoformat(),
            "watched": {c["chunk_key"].rsplit("_", 1)[0]: {"hash": c["hash"], "title": c["title"]}
                        for c in chunks if watched(t, c)},
            "all_hash": h("".join(c["hash"] for c in chunks))}


def counts_diff(old_chunks, new_chunks, art):
    """조문 하나의 항·호 개수 비교. 같거나 비교할 수 없으면 None."""
    get = lambda cs: next((c.get("counts") for c in cs if c["type"] == "article" and c["key"] == art), None)
    o, n = get(old_chunks), get(new_chunks)
    if not o or not n or o == n:
        return None
    out = []
    if o.get("항") != n.get("항"):
        out.append(f"항 {o.get('항')}→{n.get('항')}개")
    oh, nh = o.get("호", {}), n.get("호", {})
    order = lambda x: (x == "-", int(re.match(r"\d*", x).group() or 0), x)
    for hk in sorted(set(oh) | set(nh), key=order):
        a, b = oh.get(hk, 0), nh.get(hk, 0)
        if a != b:
            out.append(f"{'(항 없음)' if hk == '-' else '제' + hk + '항'} 호 {a}→{b}개")
    return ", ".join(out) or None


# ── 번호 이동 감지 ───────────────────────────────────────────────────
def nt(s):
    """제목 비교용: 띄어쓰기·가운뎃점 차이 무시"""
    return re.sub(r"[\s ㆍ·‧・]", "", str(s or ""))


def label(base, lid):
    """registry 키를 읽기 쉬운 이름으로: WMA_RULE_16의12 → 제16조의12, WMA_RULE_B5의3 → 별표 5의3"""
    k = base[len(lid) + 1:]
    return f"별표 {k[1:]}" if k.startswith("B") else f"제{k.replace('의', '조의', 1) if '의' in k else k + '조'}"


def by_base(chunks):
    """조문·별표 청크를 registry 키(chunk_key에서 시행일 뺀 것)로"""
    return {c["chunk_key"].rsplit("_", 1)[0]: c for c in chunks if c["type"] in ("article", "annex")}


def expected_titles(t, rec_watched):
    """비교할 제목: registry에 저장된 제목 + targets.py에 적은 제목(있으면 우선)"""
    lid = t["law_id"]
    rec = {k: (v or {}).get("title", "") for k, v in (rec_watched or {}).items()
           if ":" not in k and "/" not in k and not k.endswith("_전체")}
    for a in ids(t["articles"]):
        if title_of(t["articles"], a):
            rec[f"{lid}_{a}"] = title_of(t["articles"], a)
    for a in ids(t["annexes"]):
        if not a.startswith("*") and title_of(t["annexes"], a):
            rec[f"{lid}_B{a}"] = title_of(t["annexes"], a)
    return rec


def move_lines(t, rec_titles, old_chunks, new_chunks):
    """기록한 제목과 지금 제목이 다른 번호를 찾고, 같은 제목·같은 내용의 조문으로 옮겨 갔는지 보고 (고치지 않음)"""
    lid, out = t["law_id"], []
    old_map, new_map = by_base(old_chunks), by_base(new_chunks)
    for base, want in sorted(rec_titles.items()):
        if not want:
            continue
        cur = new_map.get(base)
        if cur is not None and nt(cur["title"]) == nt(want):
            continue
        is_annex = base[len(lid) + 1:].startswith("B")
        old_c = old_map.get(base)
        if old_c is not None and nt(old_c["title"]) != nt(want):
            old_c = None  # 이전 청크도 이미 다른 조문이면 내용 비교에 쓰지 않음
        now = f"'{cur['title']}'" if cur is not None else "없음"
        out.append(f"  - 🔀 번호 이동 의심: {label(base, lid)} 기록 제목 '{want}' / 지금 이 번호는 {now}")
        typ = "annex" if is_annex else "article"
        mh = move_hash(old_c) if old_c else None
        hits = []
        for k, c in new_map.items():
            if k == base or c["type"] != typ:
                continue
            same_t = nt(c["title"]) == nt(want)
            same_h = mh is not None and move_hash(c) == mh
            if same_t or same_h:
                how = "제목 같음" if same_t else "제목 다름"
                how += ", 내용 같음" if same_h else (", 내용 바뀜" if mh else ", 이전 청크 없어 내용 비교 못함")
                hits.append(f"      → {label(base, lid)} → {label(k, lid)} 이동 ({how})")
        out += hits or ["      → 같은 제목·내용의 조문을 못 찾음 (삭제 또는 제목 변경 가능성)"]
        out.append("      ※ 자동으로 고치지 않음: targets.py 번호·판정 표 근거 키를 사람이 확인해 바꿀 것")
    return out


def watched_keys(t, chunks):
    return {c["chunk_key"].rsplit("_", 1)[0] for c in chunks if watched(t, c)}


# ── 명령 ─────────────────────────────────────────────────────────────
def run_one(t):
    item = find_item(t)
    meta = meta_of(item)
    body = fetch_detail(meta)
    chunks = chunks_of(t, meta, body)
    save(t, meta, body, chunks)
    return meta, chunks


def cmd_baseline():
    reg, lines = {}, ["# 기준선 생성 결과", ""]
    for t in TARGETS:
        try:
            meta, chunks = run_one(t)
            snap = snapshot(t, meta, chunks)
            reg[t["law_id"]] = snap
            n_art = sum(c["type"] == "article" for c in chunks)
            n_anx = sum(c["type"] == "annex" for c in chunks)
            missing = [a for a in t["articles"] if f"{t['law_id']}_{a}" not in snap["watched"]]
            missing += [a for a in t["annexes"] if not a.startswith("*") and f"{t['law_id']}_B{a}" not in snap["watched"]]
            miss_parts = [spec for spec, r in parts_of(t, chunks).items() if not r["found"]]
            if miss_parts:
                missing.append(f"추출 parts 못 찾음 {miss_parts}")
            moved = move_lines(t, expected_titles(t, {}), [], chunks)
            lines.append(f"- ✅ {t['law_id']} {t['name']} | {t['grade']}등급 | 시행 {meta['effective']} | 번호 {meta['number']}"
                         f" | 조문 {n_art} · 별표 {n_anx} | 감시 {len(snap['watched'])}"
                         + (f" | ⚠️ 못 찾은 감시 대상: {missing}" if missing else ""))
            lines += moved
        except Exception as e:
            lines.append(f"- ❌ {t['law_id']} {t['name']} | {mask(e)}")
    save_registry(reg)
    write_report(lines, "baseline")


def cmd_check():
    reg = load_registry()
    if not reg:
        sys.exit("registry.json이 없습니다. 먼저 baseline을 실행하세요.")
    lines, changed = ["# 법령 변경 감지 보고서", f"실행일 {date.today()}", ""], False
    changed_ids, old_regs = [], dict(reg)
    for t in TARGETS:
        old = reg.get(t["law_id"])
        try:
            meta = meta_of(find_item(t))
        except Exception as e:
            lines.append(f"- ❌ {t['law_id']} 조회 실패: {mask(e)}")
            continue
        if old and meta["effective"] == old["effective"] and meta["serial"] == old["serial"]:
            reg[t["law_id"]]["checked"] = date.today().isoformat()
            lines.append(f"- 변경 없음 · {t['name']} (시행 {meta['effective']})")
            lines += same_version_notes(t, old, reg)
            continue
        # 이전 청크는 저장 전에 읽어 둔다 (시행일이 같고 일련번호만 바뀌면 같은 파일을 덮어쓰기 때문)
        old_chunks = chunks_at(t["law_id"], old["effective"]) if old else []
        try:
            body = fetch_detail(meta)
            chunks = chunks_of(t, meta, body)
            save(t, meta, body, chunks)
        except Exception as e:
            lines.append(f"- ❌ {t['law_id']} 원문 받기 실패 (기준선 그대로 둠): {mask(e)}")
            continue
        changed = True
        changed_ids.append(t["law_id"])
        new = snapshot(t, meta, chunks)
        if old:
            diff_w = [k for k in set(old["watched"]) | set(new["watched"])
                      if (old["watched"].get(k) or {}).get("hash") != (new["watched"].get(k) or {}).get("hash")]
            level = ("내용 변경 (감시 대상)" if diff_w else
                     "명칭·감시 외 변경" if new["all_hash"] != old["all_hash"] else "번호만 변경")
        else:
            diff_w = list(new["watched"])
            level = "신규 대상 (기준선에 추가)"
        lines += [f"## [{level} · {t['grade']}등급] {t['name']}",
                  f"- 이전: 시행 {old['effective'] if old else '-'} / 번호 {old['number'] if old else '-'}",
                  f"- 현재: 시행 {meta['effective']} / 공포 {meta['promulgated']} / 번호 {meta['number']}"]

        def part_hashes(cs, parent):
            return {c["key"]: c["hash"] for c in cs if c.get("parent") == parent}

        if old:
            for k in sorted(diff_w):
                o, n = old["watched"].get(k), new["watched"].get(k)
                state = "삭제" if not n else ("신규" if not o else "변경")
                lines.append(f"  - {state}: {k} {(n or o or {}).get('title', '')}"
                             "  → 영향 표: 법령_버전.csv의 '사용하는_표' 확인, 사람 검토 필요")
                parent = k[len(t["law_id"]) + 1:]  # "13의2" 또는 "B5"
                po, pn = part_hashes(old_chunks, parent), part_hashes(chunks, parent)
                changed_parts = [x for x in sorted(set(po) | set(pn)) if po.get(x) != pn.get(x)]
                if changed_parts:
                    lines.append(f"      바뀐 부분: {', '.join(changed_parts)}")
                if state == "변경" and not parent.startswith("B"):
                    cd = counts_diff(old_chunks, chunks, parent)
                    if cd:
                        lines.append(f"      호 개수 바뀜: {cd}")
        elif diff_w:
            lines.append(f"  - 감시 대상 {len(diff_w)}개를 기준선에 넣음")
        lines += move_lines(t, expected_titles(t, old["watched"] if old else {}), old_chunks, chunks)
        lines.append("")
        if old and diff_w and t["grade"] == 1:
            lines.append("  ⚠️ 1등급 내용 변경: 판정 표 staged 작성 + 회귀 테스트 필요 (판정 표는 자동 수정하지 않음)\n")
        reg[t["law_id"]] = new if not (old and diff_w) else {**new, "pending_review": True}
    save_registry(reg)
    extract_ids = [lid for lid in changed_ids if BY_ID[lid]["parts"]]
    if extract_ids:
        lines.append("\n## 발췌 갱신 (원문만, 판정 표는 그대로)")
        cmd_extract(extract_ids, only_ids=True)
        for lid in extract_ids:
            old_eff = (old_regs.get(lid) or {}).get("effective", "-")
            lines.append(f"- {lid}: watched/{lid}.md 갱신 · 이전 발췌 watched/history/{lid}_{old_eff}.md 와 비교")
    if not changed:
        lines.append("\n전체 변경 없음")
    write_report(lines, "check")


def same_version_notes(t, old, reg):
    """원문은 그대로인데 targets.py가 바뀐 경우: 감시 목록 차이·제목 차이를 보고하고 기준선의 감시 목록만 다시 만든다."""
    lid = t["law_id"]
    chunks = chunks_at(lid, old["effective"])
    if not chunks:
        return [f"  - ⚠️ {lid} 청크 파일(시행 {old['effective']})이 없어 감시 목록을 비교 못함"]
    out = move_lines(t, expected_titles(t, old["watched"]), chunks, chunks)
    now = watched_keys(t, chunks)
    if now != set(old["watched"]):
        add, rem = sorted(now - set(old["watched"])), sorted(set(old["watched"]) - now)
        out.append(f"  - 📝 targets.py 감시 목록 바뀜 → 기준선 감시 목록 다시 만듦"
                   + (f" | 추가 {[k[len(lid) + 1:] for k in add]}" if add else "")
                   + (f" | 빠짐 {[k[len(lid) + 1:] for k in rem]}" if rem else ""))
        meta = {k: old.get(k, "") for k in ("serial", "id", "effective", "promulgated", "number")}
        snap = snapshot(t, meta, chunks)
        reg[lid] = {**snap, **({"pending_review": True} if old.get("pending_review") else {})}
    return out


def cmd_titles(args):
    """감시 조문·별표의 지금 제목을 targets.py에 붙여 넣을 형식으로"""
    for t in pick(args) if args else TARGETS:
        chunks = latest_chunks(t["law_id"])
        if not chunks:
            print(f"# {t['law_id']}: 청크 없음")
            continue
        m = by_base(chunks)
        get = lambda k: (m.get(f"{t['law_id']}_{k}") or {}).get("title", "") or "(없음)"
        arts = {a: get(a) for a in ids(t["articles"])}
        anx = {a: get("B" + a) for a in ids(t["annexes"]) if not a.startswith("*")}
        print(f"# {t['law_id']} {t['name']} (시행 {chunks[0]['effective']})")
        print(f"articles={json.dumps(arts, ensure_ascii=False)},")
        if anx:
            print(f"annexes={json.dumps(anx, ensure_ascii=False)},")
        for a in ids(t["articles"]):
            want = title_of(t["articles"], a)
            if want and nt(want) != nt(arts[a]):
                print(f"#  ⚠️ {a}: targets.py 제목 '{want}' ≠ 지금 '{arts[a]}'")
        print()


RX_REF = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*):([^@#;,\s\"]+)@(\d{8})(?:#p\d+)?(?:#([0-9a-f]{16}))?")


def ref_target(spec):
    """근거 키의 spec → (청크 종류, 번호). 제목으로 찾는 별표·행정규칙 본문은 None"""
    if spec.startswith("B"):
        k = re.split(r"[:@]", spec[1:])[0]
        return None if k.startswith("*") else ("annex", k)
    if spec == "*" or spec.startswith("§"):
        return None
    return ("article", spec.split("/")[0])


def cmd_refs(args):
    """판정 표 CSV의 근거 키(LAW:KEY@날짜#hash)를 지금 원문과 대조: 근거 조문 이동 의심 / 근거 내용 바뀜. 표는 고치지 않음."""
    paths = [Path(a) for a in args] or [ROOT.parent / "tables"]
    files = sorted({f for p in paths for f in ([p] if p.is_file() else p.rglob("*.csv"))})
    refs = {}
    for f in files:
        try:
            rows = list(csv.reader(f.open(encoding="utf-8-sig")))
        except Exception as e:
            print(f"읽기 실패 {f}: {e}")
            continue
        for row in rows:
            for cell in row:
                for m in RX_REF.finditer(cell):
                    if m.group(1) in BY_ID:
                        refs.setdefault(m.groups(), set()).add(f.name)
    lines = ["# 판정 표 근거 키 점검", f"실행일 {date.today()} · CSV {len(files)}개 · 근거 키 {len(refs)}종", ""]
    cur_cache, n_bad = {}, 0
    for (lid, spec, eff, hsh), where in sorted(refs.items()):
        cur = cur_cache.setdefault(lid, latest_chunks(lid))
        if not cur:
            lines.append(f"- ❌ {lid}:{spec}@{eff} — 지금 청크 없음")
            n_bad += 1
            continue
        cur_eff = cur[0]["effective"]
        notes = []
        tg = ref_target(spec)
        if tg:
            typ, k = tg
            then = next((c for c in chunks_at(lid, eff) if c["type"] == typ and c["key"] == k), None)
            now = next((c for c in cur if c["type"] == typ and c["key"] == k), None)
            if then is None:
                notes.append(f"그 시행일({eff}) 청크가 없어 제목 비교 못함")
            elif now is None or nt(now["title"]) != nt(then["title"]):
                to = [c["key"] for c in cur if c["type"] == typ and nt(c["title"]) == nt(then["title"])]
                notes.append(f"🔀 근거 조문 이동 의심: '{then['title']}' → 지금 이 번호는 '{now['title'] if now else '없음'}'"
                             + (f" / 같은 제목: {', '.join(('B' if typ == 'annex' else '') + x for x in to)}" if to else ""))
        if hsh:
            title, text = select_part(cur, spec)
            if title is None:
                notes.append(f"지금 원문에서 {spec} 못 찾음")
            elif h(text) != hsh:
                notes.append(f"근거 내용 바뀜 (지금 시행 {cur_eff})")
        if notes:
            n_bad += 1
            lines.append(f"- {lid}:{spec}@{eff} ({', '.join(sorted(where))})")
            lines += [f"    - {x}" for x in notes]
    lines.append(f"\n확인 필요 {n_bad}건 / 전체 {len(refs)}종 (판정 표는 자동 수정하지 않음)")
    write_report(lines, "refs")


def cmd_inspect(law_id):
    files = sorted((ROOT / "raw" / law_id).glob("*.json"))
    if not files:
        sys.exit("저장된 원문이 없습니다. baseline을 먼저 실행하세요.")
    body = json.loads(files[-1].read_text(encoding="utf-8"))

    def tree(o, depth=0):
        pad = "  " * depth
        if isinstance(o, dict):
            for k, v in list(o.items())[:25]:
                kind = type(v).__name__ + (f"[{len(v)}]" if isinstance(v, list) else "")
                print(f"{pad}{k}: {kind}" + (f" = {mask(v[:40])!r}" if isinstance(v, str) else ""))
                if depth < 4:
                    tree(v, depth + 1)
        elif isinstance(o, list) and o:
            tree(o[0], depth)
    tree(body)


def cmd_extract(args, only_ids=False):
    """args: [법령ID] [부분 …] [--pdf]. only_ids=True면 args 전체가 법령ID 목록 (check에서 부름)."""
    want_pdf = "--pdf" in args
    args = [a for a in args if a != "--pdf"]
    if only_ids:
        ids, only_specs = set(args), set()
    else:
        ids, only_specs = ({args[0]} if args else set()), set(args[1:])
    if ids:
        pick(sorted(ids))  # 없는 ID면 오류
    out_dir = ROOT / "watched"
    out_dir.mkdir(parents=True, exist_ok=True)
    all_rows, summary = [], ["# 필요한 부분 추출 결과", ""]
    for t in TARGETS:
        if ids and t["law_id"] not in ids:
            continue
        if not t["parts"]:
            continue  # 2등급 참고 법령(원문만 받아 둠)은 발췌 없음
        chunks = latest_chunks(t["law_id"])
        if not chunks:
            summary.append(f"- ❌ {t['law_id']}: 저장된 원문 없음 (baseline 먼저)")
            continue
        eff = chunks[0]["effective"]
        res = parts_of(t, chunks)
        if only_specs:
            res = {k: v for k, v in res.items() if k in only_specs}
        rows, md, miss = [], [f"# {t['name']} (시행 {eff})", ""], []
        for spec, r in res.items():
            if not r["found"]:
                miss.append(f"{spec}: {r['reason']}")
                continue
            row = {"law_id": t["law_id"], "spec": spec, "chunk_key": f"{t['law_id']}:{spec}@{eff}",
                   "title": r["title"], "hash": r["hash"], "effective": eff, "text": r["text"]}
            rows.append(row)
            md += [f"## {spec} · {r['title']}", f"`{row['chunk_key']}` · hash {r['hash']}", "", r["text"].strip(), ""]
        if want_pdf:
            pdf_dir = out_dir / "pdf"
            pdf_dir.mkdir(exist_ok=True)
            for spec in res:
                if not spec.startswith("B"):
                    continue
                key = re.split(r"[:@]", spec[1:])[0]
                c = next((c for c in chunks if c["type"] == "annex" and
                          (c["key"] == key or (key.startswith("*") and key[1:] in str(c["title"])))), None)
                if c and c.get("pdf"):
                    dest = pdf_dir / f"{t['law_id']}_B{c['key']}_{eff}.pdf"
                    if not dest.exists():
                        try:
                            download(c["pdf"], dest)
                        except Exception as e:
                            miss.append(f"{spec} PDF: {mask(e)}")
        with open(out_dir / f"{t['law_id']}.jsonl", "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        (out_dir / f"{t['law_id']}.md").write_text("\n".join(md), encoding="utf-8")
        hist = out_dir / "history"
        hist.mkdir(exist_ok=True)
        if not only_specs:  # 시행일별 발췌 보관 (덮어쓰지 않음)
            for ext, body in (("md", "\n".join(md)), ("jsonl", "\n".join(json.dumps(r, ensure_ascii=False) for r in rows))):
                hp = hist / f"{t['law_id']}_{eff}.{ext}"
                if not hp.exists():
                    hp.write_text(body, encoding="utf-8")
        all_rows += rows
        summary.append(f"- {'✅' if not miss else '⚠️'} {t['law_id']} {t['name']}: {len(rows)}건"
                           + (f" | 못 찾음: {miss}" if miss else ""))
    rebuild_all(out_dir)
    summary.append(f"\n이번 추출 {len(all_rows)}건 → {out_dir} (이전 시행일 발췌는 watched/history/에 보관)")
    print("\n".join(summary))
    return all_rows


def rebuild_all(out_dir):
    rows = []
    for t in TARGETS:
        f = out_dir / f"{t['law_id']}.jsonl"
        if f.exists():
            rows += [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    (out_dir / "watched_all.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "baseline":
        cmd_baseline()
    elif cmd == "check":
        cmd_check()
    elif cmd == "inspect" and len(sys.argv) > 2:
        cmd_inspect(sys.argv[2])
    elif cmd == "extract":
        cmd_extract(sys.argv[2:])
    elif cmd == "titles":
        cmd_titles(sys.argv[2:])
    elif cmd == "refs":
        cmd_refs(sys.argv[2:])
    else:
        print(__doc__)