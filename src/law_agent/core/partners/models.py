# route_agent/src/partners/models.py 에서 복사 (2026.11.17). 바꾼 점: import 경로
# route_agent 는 비교 기준이라 import 하지 않고 law_agent 안에 둔다. 원본이 바뀌어도 자동으로 따라가지 않는다.
"""업체 매칭 결과 자료형.

경로(RouteDraft)의 단계마다 StepMatch 1개, 경로마다 RouteMatch 1개.
관문 결과(RouteDraft)는 건드리지 않고 옆에 따로 붙인다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Location:
    lat: float | None
    lng: float | None
    basis: str                       # 업체 좌표 / 시군구 중심점 / 시도 중심점 / 없음
    distance_km: float | None = None  # 배출 사업장(시군구 중심점)에서 직선거리
    road_km: float | None = None      # 카카오 도로 거리 (조회했을 때만, 2026.11.18)
    duration_min: int | None = None   # 카카오 예상 시간
    toll_won: int | None = None       # 카카오 통행료
    road_basis: str | None = None     # "카카오 도로" / None(= 직선 × 1.3 추정)


@dataclass
class PartnerCandidate:
    company_id: str                  # 업체_키 "미래환경수지|군산시"
    name: str                        # 업체명 (원자료 표기)
    role: str                        # recycler / preprocessor / transporter / treatment / end_user
    sido: str
    sigungu: str
    address: str
    phone: str | None
    data_date: str                   # 기준연도
    source: str                      # 출처 (파일#시트#행)
    permit_type: str | None = None   # 허가 / 신고 (재활용 업체)
    registration_type: str = ""      # 등록형태 또는 업종·대상군
    waste_group: str | None = None   # "51-03 폐합성고분자화합물"
    treatment_codes: list[str] = field(default_factory=list)
    r_codes: list[str] = field(default_factory=list)
    accepts_designated: list[str] = field(default_factory=list)   # 지정 분류번호 앞자리, * = 전체
    scope_text: str | None = None    # 운반·처분 영업대상 원문
    capacity: dict | None = None
    match_confidence: str | None = None   # end_user 품목 연결 확신도
    location: Location | None = None
    reasons: list[str] = field(default_factory=list)
    status: str = "needs_info"       # 회신 전에는 항상 needs_info
    evidence: list[dict] = field(default_factory=list)   # 통과 근거 (CSV 값 + 법) — evidence.py (2026.11.20)
    cautions: list[str] = field(default_factory=list)    # CSV 로 못 거르는 확인할 것 (배출시설계·용량·자료 연도 …)


@dataclass
class StepMatch:
    step_order: int
    role: str
    candidates: list[PartnerCandidate] = field(default_factory=list)   # 정렬 후 상위 N개
    total_found: int = 0                                              # 조건을 맞춘 전체 업체 수
    rejected_summary: dict[str, int] = field(default_factory=dict)    # {뺀 이유: 업체 수}
    questions: list[dict] = field(default_factory=list)
    optional: bool = False           # 관문 1 운반("필요 시")처럼 없어도 경로가 되는 단계


@dataclass
class RouteMatch:
    route_index: int
    steps: list[StepMatch] = field(default_factory=list)

    pending: bool = False            # 아직 매칭을 연결하지 않은 역할이 있음

    @property
    def secured(self) -> bool | None:
        """필수 단계마다 후보가 1곳 이상 있는지.
        True = 확보, False = 미확보, None = 매칭하지 않음(불가 경로·업체 없는 경로·연결 안 된 역할)"""
        if self.pending or not self.steps:
            return None
        return all(s.candidates for s in self.steps if not s.optional)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["secured"] = self.secured
        return d
