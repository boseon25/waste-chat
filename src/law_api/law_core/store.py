"""저장 위치와 형식 (pipeline의 lawstore가 읽는 형식)

ROOT (data/law 하나만 쓴다)
  - 환경변수 WASTE_LAW_DIR이 있으면: 그 폴더 이름이 chunks·chunk_v2면 상위 폴더, 아니면 그 폴더
  - 없으면 프로젝트 루트의 data/law
  - WASTE_DATA_DIR은 쓰지 않는다 (서버에서 data/tables/active라 판정표 폴더 안에 저장되는 문제가 있었음)

ROOT/
  raw/{law_id}/{시행일}_{일련번호}.json   원문 (공유·업로드 금지, gitignore)
  chunk_v2/{law_id}/{시행일}.jsonl        청크 (시행일별로 남김, 폴더 이름은 CHUNK_DIRNAME)
  registry.json                           기준선 (공유 금지)
  reports/{날짜}_{종류}.md                보고서
  watched/                                발췌
  all_chunks.jsonl, summary.json          law_chunk 실행 결과 (청크 폴더 밖)

저장 전에 문자열 전체를 mask()에 통과시켜 인증키가 파일에 남지 않게 한다.
"""
import json, os
from datetime import date
from pathlib import Path

from .api import mask


CHUNK_DIRNAME = "chunk_v2"   # 청크 폴더 이름 (예전: "chunks"). lawstore가 읽는 폴더 이름과 맞춰야 함
LEGACY_DIRNAME = "chunks"    # 읽을 때만: chunk_v2에 없으면 예전 폴더에서 찾음


def _root() -> Path:
    v = os.environ.get("WASTE_LAW_DIR")
    if v:
        p = Path(v).expanduser()
        return p.parent if p.name in ("chunks", CHUNK_DIRNAME) else p
    return Path(__file__).resolve().parents[3] / "data" / "law"  # law_core → 폐기물법령… → src → 프로젝트


ROOT = _root()


def _dump(o) -> str:
    return mask(json.dumps(o, ensure_ascii=False))


def save_raw(t, meta, body) -> Path:
    d = ROOT / "raw" / t["law_id"]
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{meta['effective'] or 'unknown'}_{meta['serial']}.json"
    p.write_text(mask(json.dumps(body, ensure_ascii=False, indent=1)), encoding="utf-8")
    return p


def save_chunks(t, chunks, effective) -> Path:
    d = ROOT / CHUNK_DIRNAME / t["law_id"]
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{effective or 'unknown'}.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(_dump(ch) + "\n")
    return p


def save(t, meta, body, chunks) -> Path:
    """원문 + 청크 저장. 청크 파일 경로를 돌려준다."""
    save_raw(t, meta, body)
    return save_chunks(t, chunks, meta["effective"])


def save_all_chunks(chunks) -> Path:
    ROOT.mkdir(parents=True, exist_ok=True)
    p = ROOT / "all_chunks.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(_dump(ch) + "\n")
    return p


def registry_path() -> Path:
    return ROOT / "registry.json"


def load_registry() -> dict:
    p = registry_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_registry(reg):
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(mask(json.dumps(reg, ensure_ascii=False, indent=1)), encoding="utf-8")


def read_chunks(path: Path) -> list:
    if not path or not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def chunks_at(law_id, effective) -> list:
    """특정 시행일 청크 (chunk_v2 → 예전 chunks 순서로 찾고, 없으면 빈 목록)"""
    for dn in (CHUNK_DIRNAME, LEGACY_DIRNAME):
        p = ROOT / dn / law_id / f"{effective}.jsonl"
        if p.exists():
            return read_chunks(p)
    return []


def latest_chunks(law_id) -> list:
    for dn in (CHUNK_DIRNAME, LEGACY_DIRNAME):
        files = sorted((ROOT / dn / law_id).glob("*.jsonl"))
        if files:
            return read_chunks(files[-1])
    return []


def write_report(lines, kind) -> Path:
    d = ROOT / "reports"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{date.today()}_{kind}.md"
    text = mask("\n".join(lines))
    p.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\n보고서: {p}")
    return p