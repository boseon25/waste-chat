"""Known regions of the distributed blank forms, in visible PDF points from top left."""
from hashlib import sha256
from . import form13_cells, other_cells

TEMPLATES = {
    form13_cells.DOC_ID: (form13_cells.SHA256, form13_cells.CELLS),
    **other_cells.MAPS,
}


def attach_to_catalog(documents, root):
    for doc_id, (expected, cells) in TEMPLATES.items():
        doc = documents[doc_id]
        if sha256((root / doc["file"]).read_bytes()).hexdigest() == expected:
            doc["original_cells"] = cells


def cells_for_export(doc_id, source):
    if not isinstance(doc_id, str) or doc_id not in TEMPLATES:
        raise ValueError("칸이 속한 원본 서식을 다시 선택해 주세요.")
    expected, cells = TEMPLATES[doc_id]
    if sha256(source).hexdigest() != expected:
        raise ValueError("선택한 칸과 PDF의 판본이 다릅니다. 동봉된 원본에서 다시 선택하세요.")
    return {cell["key"]: cell for cell in cells}
