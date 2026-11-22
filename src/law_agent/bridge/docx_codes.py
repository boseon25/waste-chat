"""입력서의 '분류번호' 칸(태그 waste_N_code) 읽기. (waste-chat 결합 때 web/user.py 에서 옮김 — 관리자·사용자 웹 화면은 뺐다)"""
from __future__ import annotations

import re
from pathlib import Path

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
RX_CODE = re.compile(r"^\d{2}-\d{2}-\d{2}$")


def docx_codes(path: Path) -> dict[int, str]:
    """입력서의 '분류번호 (가상)' 칸(태그 waste_N_code) → {표 번호: 분류번호}. 칸이 없거나 비면 빈 dict."""
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml")
        if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
            return {}
        root = ET.fromstring(xml)
    except Exception:
        return {}
    body = root.find(f"{W_NS}body")
    tables = body.findall(f"{W_NS}tbl") if body is not None else []
    out = {}
    for ti, tbl in enumerate(tables):
        for sdt in tbl.iter(f"{W_NS}sdt"):
            tag = sdt.find(f"{W_NS}sdtPr/{W_NS}tag")
            if tag is None or not re.fullmatch(r"waste_\d+_code", tag.get(f"{W_NS}val", "")):
                continue
            text = "".join(t.text or "" for t in sdt.iter(f"{W_NS}t")).strip()
            if RX_CODE.match(text):
                out[ti] = text
    return out
