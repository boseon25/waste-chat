#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""대상 법령 전부 받아 청크 저장 + 실행 요약

    python law_chunk.py              # 전부
    python law_chunk.py WMA CWA      # 고른 법령만 (테스트용)

저장 (law_core/store.py)
    data/law/raw/{law_id}/{시행일}_{일련번호}.json
    data/law/chunk_v2/{law_id}/{시행일}.jsonl
    data/law/all_chunks.jsonl, data/law/summary.json   (청크 폴더 밖)
"""
import json, sys

from law_core.targets import TARGETS, pick
from law_core.api import mask, find_item, meta_of, fetch_detail
from law_core.chunker import chunks_of
from law_core import store


def run_one(target):
    meta = meta_of(find_item(target))
    body = fetch_detail(meta)
    chunks = chunks_of(target, meta, body)
    path = store.save(target, meta, body, chunks)
    print(f"[완료] {target['law_id']} | {target['name']} | 시행일 {meta['effective']} | 청크 {len(chunks)}개 | {path}")
    return meta, chunks


def run_all(targets=TARGETS):
    all_chunks, success, failed = [], [], []
    for i, t in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}] {t['law_id']} - {t['name']}")
        try:
            meta, chunks = run_one(t)
            all_chunks.extend(chunks)
            success.append({"law_id": t["law_id"], "name": t["name"], "grade": t["grade"],
                            "effective": meta["effective"], "chunks": len(chunks)})
        except Exception as exc:
            err = mask(exc)
            failed.append({"law_id": t["law_id"], "name": t["name"], "error": err})
            print(f"[실패] {t['law_id']} | {err}")

    all_path = store.save_all_chunks(all_chunks)
    report = {"total_targets": len(targets), "success_count": len(success), "failed_count": len(failed),
              "total_chunks": len(all_chunks), "success": success, "failed": failed}
    report_path = store.ROOT / "summary.json"
    report_path.write_text(mask(json.dumps(report, ensure_ascii=False, indent=2)), encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"전체 법령: {len(targets)}개 | 성공: {len(success)}개 | 실패: {len(failed)}개")
    print(f"전체 청크: {len(all_chunks)}개")
    print(f"저장 위치: {store.ROOT}")
    print(f"통합 파일: {all_path}")
    print(f"실행 요약: {report_path}")
    if failed:
        print("\n실패 법령 (이름이 API 검색 결과와 정확히 같은지 확인):")
        for item in failed:
            print(f"- {item['law_id']} | {item['error']}")


if __name__ == "__main__":
    run_all(pick(sys.argv[1:]))