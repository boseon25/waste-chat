"""파트 2 결과 → 다른 파트가 받는 형식 (계약 v1.1, 파트3_수신값_파트2_v1.1.md).

  proposals = to_route_proposals(result, raw_case, case, tables_dir)   # → 파트 3
  info_req  = build_information_request(result, proposals)            # → 파트 1 (배출업체에 되물을 것)
  proposals = apply_feedback(proposals, feedbacks)                    # 업체 회신 반영
  proposals = mark_stale(proposals, current_case_version)

판정·매칭은 하지 않는다. pipeline이 만든 결과를 정해진 형식으로 옮기고, 회신을 반영할 뿐이다.
"""
from .feedback import apply_feedback
from .info_request import build_information_request
from .route_proposal import SCHEMA_VERSION, assign_route_ids, mark_stale, route_id, to_route_proposals

__all__ = ["to_route_proposals", "build_information_request", "apply_feedback", "mark_stale",
           "route_id", "assign_route_ids", "SCHEMA_VERSION"]
