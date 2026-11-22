"""WasteCase 2.0 / M1 contract proposal. Not a publication or authorization service."""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


def now():
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True, allow_inf_nan=False)


ID = Annotated[str, Field(min_length=1)]
T = TypeVar('T')
Applicability = Literal['matched', 'unknown', 'mismatched']


class Fact(Model, Generic[T]):
    value: T | None = None
    raw_value: str | None = None
    status: Literal['confirmed', 'needs_info', 'conditional'] = 'needs_info'
    basis: Literal['user_report', 'document', 'measurement', 'observation', 'derived'] | None = None
    verification: Literal['unverified', 'user_confirmed', 'source_checked', 'conflicted'] = 'unverified'
    evidence_ids: list[ID] = Field(default_factory=list)
    applicability: Applicability = 'unknown'
    updated_at: datetime = Field(default_factory=now)

    @model_validator(mode='after')
    def coherent(self):
        if self.value is not None and (not self.basis or not self.evidence_ids):
            raise ValueError('non-null fact requires basis and evidence')
        if self.status == 'confirmed' and (
            self.value is None or self.verification not in ('user_confirmed', 'source_checked')
            or self.applicability != 'matched' or not self.evidence_ids
        ):
            raise ValueError('confirmed requires matching evidence and confirmation procedure')
        if self.value is not None and self.applicability == 'mismatched':
            raise ValueError('mismatched claims must remain held assertions, not accepted facts')
        return self


class Locator(Model):
    kind: Literal['docx', 'pdf', 'message', 'image', 'rule', 'file']
    table: int | None = Field(default=None, ge=0)
    row: int | None = Field(default=None, ge=0)
    cell: int | None = Field(default=None, ge=0)
    tag: str | None = None
    paragraph: int | None = Field(default=None, ge=0)
    page: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    message_id: ID | None = None
    section: str | None = None

    @model_validator(mode='after')
    def position(self):
        if self.kind == 'docx':
            in_table = None not in (self.table, self.row, self.cell)
            in_paragraph = self.paragraph is not None
            if not in_table and not in_paragraph:
                raise ValueError('DOCX requires table/row/cell or paragraph, zero-based')
        if self.kind == 'pdf' and (self.page is None or self.bbox is None):
            raise ValueError('PDF requires page (one-based) and bbox')
        if self.bbox and (self.bbox[0] > self.bbox[2] or self.bbox[1] > self.bbox[3]):
            raise ValueError('invalid bbox')
        if self.kind == 'message' and not self.message_id:
            raise ValueError('message_id required')
        return self


class Evidence(Model):
    evidence_id: ID
    kind: Literal['input_document', 'user_message', 'photo', 'analysis', 'measurement', 'law', 'reported_document']
    file_id: ID | None = None
    message_id: ID | None = None
    sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    source_locator: Locator
    raw_excerpt: str
    document_date: date | None = None
    issuer: str | None = None
    sample_id: str | None = None
    sampled_at: date | None = None
    applicable_case_ids: list[ID] = Field(default_factory=list)
    applicability: Applicability = 'unknown'
    checked_at: datetime | None = None

    @model_validator(mode='after')
    def origin(self):
        if not self.file_id and not self.message_id:
            raise ValueError('private file_id or message_id required')
        if self.file_id and not self.sha256:
            raise ValueError('file evidence requires hash')
        if self.source_locator.kind == 'message' and self.message_id != self.source_locator.message_id:
            raise ValueError('message locator mismatch')
        return self


UNITS = {'kg': 'mass', 't': 'mass', 'g': 'mass', 'L': 'volume', 'm3': 'volume', '개': 'count', '포대': 'count'}
Unit = Literal['kg', 't', 'g', 'L', 'm3', '개', '포대']
Dimension = Literal['mass', 'volume', 'count']


class Quantity(Fact[Decimal]):
    unit: Unit | None = None
    dimension: Dimension | None = None
    scope: Literal['current_lot'] = 'current_lot'
    measured_at: Fact[date] = Field(default_factory=Fact[date])
    method: Fact[str] = Field(default_factory=Fact[str])
    net_weight: Fact[StrictBool] = Field(default_factory=Fact[StrictBool])
    conversion_rule: str | None = None

    @model_validator(mode='after')
    def units(self):
        if self.value is not None and (self.value < 0 or not self.unit):
            raise ValueError('nonnegative value and unit required')
        if self.unit and UNITS[self.unit] != self.dimension:
            raise ValueError('unit/dimension mismatch')
        if self.dimension and not self.unit:
            raise ValueError('unit missing')
        if self.dimension == 'count' and self.value is not None and self.value != self.value.to_integral_value():
            raise ValueError('count must be an integer')
        return self


class Generation(Model):
    amount: Fact[Decimal] = Field(default_factory=Fact[Decimal])
    unit: Fact[Unit] = Field(default_factory=Fact[Unit])
    period: Fact[Literal['day', 'week', 'month', 'year']] = Field(default_factory=Fact[Literal['day', 'week', 'month', 'year']])
    recurring: Fact[StrictBool] = Field(default_factory=Fact[StrictBool])

    @model_validator(mode='after')
    def amount_nonnegative(self):
        if self.amount.value is not None and self.amount.value < 0:
            raise ValueError('negative generation amount')
        return self


class Component(Model):
    name: Fact[str]
    proportion_percent: Fact[Decimal] = Field(default_factory=Fact[Decimal])

    @model_validator(mode='after')
    def percentage(self):
        p = self.proportion_percent.value
        if p is not None and not 0 <= p <= 100:
            raise ValueError('percentage outside 0..100')
        return self


class AnalysisResult(Model):
    result_id: ID
    substance: str
    value: Decimal | None = None
    raw_value: str
    qualifier: Literal['measured', 'not_detected', 'below_quantification', 'unknown']
    unit: str | None = None
    test_method: str | None = None
    detection_limit: Decimal | None = Field(default=None, ge=0)
    sampled_at: date | None = None
    sample_id: str | None = None
    concentration_type: Literal['content', 'leachate', 'other', 'unknown'] = 'unknown'
    sample_basis: Literal['dry', 'wet', 'unknown'] = 'unknown'
    evidence_ids: list[ID] = Field(min_length=1)
    applicability: Applicability = 'unknown'

    @model_validator(mode='after')
    def value_semantics(self):
        if self.qualifier != 'measured' and self.value is not None:
            raise ValueError('non-quantified result must not become zero/exact value')
        if self.qualifier == 'measured' and (self.value is None or not self.unit):
            raise ValueError('measured result requires value and unit')
        if self.value is not None and self.value < 0:
            raise ValueError('negative concentration')
        return self


class ConditionResult(Model):
    condition_id: ID
    result: Literal['true', 'false', 'unknown']
    fact_refs: list[str] = Field(default_factory=list)
    reason: str


class ClassificationCandidate(Model):
    code: str
    official_name: str
    rule_id: ID
    source_id: ID
    source_locator: str
    state: Literal['candidate', 'supported', 'rejected'] = 'candidate'
    conditions: list[ConditionResult] = Field(min_length=1)
    exclusion_reason: str | None = None

    @model_validator(mode='after')
    def rejected_reason(self):
        if self.state == 'rejected' and not self.exclusion_reason:
            raise ValueError('rejected candidate requires reason')
        return self


class Classification(Model):
    reported_code: Fact[str] = Field(default_factory=Fact[str])
    candidates: list[ClassificationCandidate] = Field(default_factory=list)
    selected_code: str | None = None
    rule_version: str | None = None
    review_required: bool = True

    @model_validator(mode='after')
    def selection(self):
        if self.selected_code:
            supported = [c for c in self.candidates if c.code == self.selected_code and c.state == 'supported'
                         and all(x.result == 'true' for x in c.conditions)]
            if not supported or self.review_required or not self.rule_version:
                raise ValueError('selected code requires supported reviewed rule')
        return self


class Assertion(Model):
    assertion_id: ID
    field_path: str
    raw_value: str
    normalized_value_json: str | None = None
    evidence_ids: list[ID] = Field(min_length=1)
    applicability: Applicability = 'unknown'
    disposition: Literal['proposed', 'held', 'accepted'] = 'proposed'
    reason: str


class Conflict(Model):
    conflict_id: ID
    field_path: str
    assertion_ids: list[ID] = Field(min_length=2)
    status: Literal['open', 'resolved'] = 'open'
    reason: str
    resolution: str | None = None

    @model_validator(mode='after')
    def claims(self):
        if len(set(self.assertion_ids)) < 2:
            raise ValueError('two distinct claims required')
        if self.status == 'resolved' and not self.resolution:
            raise ValueError('resolution reason required')
        return self


class InformationRequest(Model):
    request_id: ID
    case_id: ID
    based_on_case_version: int = Field(ge=0)
    requested_by: Literal['part1', 'part2', 'part3']
    fields: list[str] = Field(min_length=1)
    trigger_rule_id: ID
    reason: str
    question: str
    answer_type: Literal['text', 'single_choice', 'number', 'date', 'file']
    options: list[str] = Field(default_factory=list)
    acceptable_evidence: list[str] = Field(default_factory=list)
    guidance_id: ID | None = None
    blocking_scope: str = Field(pattern=r'^(all|classification|route:[^\s:]+|matching:[^\s:]+|document:[^\s:]+)$')
    status: Literal['open', 'answered', 'resolved', 'unavailable'] = 'open'
    answer_evidence_ids: list[ID] = Field(default_factory=list)
    resolution_reason: str | None = None

    @model_validator(mode='after')
    def answer(self):
        if self.status != 'open' and not self.answer_evidence_ids:
            raise ValueError('answer/unavailable requires evidence')
        if self.status == 'resolved' and not self.resolution_reason:
            raise ValueError('resolved requires validation reason')
        return self


class StageReadiness(Model):
    status: Literal['ready', 'conditional', 'blocked', 'not_assessed'] = 'not_assessed'
    reasons: list[str] = Field(default_factory=list)
    assessed_by: Literal['part1', 'part2', 'part3'] | None = None

    @model_validator(mode='after')
    def assessment(self):
        if self.status != 'not_assessed' and (not self.reasons or not self.assessed_by):
            raise ValueError('readiness requires reasons and assessor')
        return self


class Readiness(Model):
    route_exploration: StageReadiness = Field(default_factory=StageReadiness)
    partner_matching: StageReadiness = Field(default_factory=StageReadiness)
    document_drafting: StageReadiness = Field(default_factory=StageReadiness)

    @model_validator(mode='after')
    def owners(self):
        for stage in (self.route_exploration, self.partner_matching):
            if stage.status != 'not_assessed' and stage.assessed_by != 'part2':
                raise ValueError('route/matching assessed by part2')
        if self.document_drafting.status != 'not_assessed' and self.document_drafting.assessed_by != 'part3':
            raise ValueError('document assessed by part3')
        return self


class WasteCase(Model):
    schema_version: Literal['2.0'] = '2.0'
    case_id: ID
    case_version: int = Field(default=0, ge=0)
    draft_revision: int = Field(default=0, ge=0)
    company_id: ID
    site_id: ID | None = None
    submission_id: ID
    lot_id: ID
    synthetic: bool
    name: Fact[str] = Field(default_factory=Fact[str])
    process_description: Fact[str] = Field(default_factory=Fact[str])
    industry: Fact[str] = Field(default_factory=Fact[str])
    physical_state: Fact[str] = Field(default_factory=Fact[str])
    components: list[Component] = Field(default_factory=list)
    mixture_type: Fact[Literal['single', 'mixed', 'composite', 'multiple_unspecified']] = Field(default_factory=Fact[Literal['single', 'mixed', 'composite', 'multiple_unspecified']])
    separability: Fact[StrictBool] = Field(default_factory=Fact[StrictBool])
    quantity: Quantity = Field(default_factory=Quantity)
    generation: Generation = Field(default_factory=Generation)
    visible_contamination: Fact[Literal['not_visible', 'partial', 'widespread']] = Field(default_factory=Fact[Literal['not_visible', 'partial', 'widespread']])
    hazard_declaration: Fact[StrictBool] = Field(default_factory=Fact[StrictBool])
    substances: list[Fact[str]] = Field(default_factory=list)
    analysis_results: list[AnalysisResult] = Field(default_factory=list)
    classification: Classification = Field(default_factory=Classification)
    evidence: list[Evidence] = Field(default_factory=list)
    assertions: list[Assertion] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    information_requests: list[InformationRequest] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    readiness: Readiness = Field(default_factory=Readiness)
    extractor_version: str = 'not_run'
    rule_version: str = 'none'
    source_form_date: Fact[date] = Field(default_factory=Fact[date])
    site_inherited: bool = False
    site_source_evidence_ids: list[ID] = Field(default_factory=list)

    @model_validator(mode='after')
    def integrity(self):
        evidence = {e.evidence_id: e for e in self.evidence}
        if len(evidence) != len(self.evidence):
            raise ValueError('duplicate evidence')
        def walk(x):
            if isinstance(x, BaseModel):
                for key in type(x).model_fields:
                    value = getattr(x, key)
                    if key in ('evidence_ids', 'answer_evidence_ids', 'site_source_evidence_ids'):
                        if set(value) - set(evidence):
                            raise ValueError('unknown evidence reference')
                    elif key != 'evidence':
                        walk(value)
                if isinstance(x, Fact) and x.value is not None and x.applicability == 'matched':
                    for ref in x.evidence_ids:
                        e = evidence[ref]
                        if e.applicability != 'matched' or self.case_id not in e.applicable_case_ids:
                            raise ValueError('evidence does not match this case')
            elif isinstance(x, list):
                for item in x:
                    walk(item)
        walk(self)
        assertions = {a.assertion_id: a for a in self.assertions}
        if len(assertions) != len(self.assertions):
            raise ValueError('duplicate assertion')
        for c in self.conflicts:
            if set(c.assertion_ids) - set(assertions):
                raise ValueError('unknown conflict assertion')
            if any(assertions[a].field_path != c.field_path for a in c.assertion_ids):
                raise ValueError('conflict claims must reference same field')
        request_ids = [r.request_id for r in self.information_requests]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError('duplicate request ID')
        for r in self.information_requests:
            if r.case_id != self.case_id or r.based_on_case_version > self.case_version:
                raise ValueError('request belongs to other case/future version')
        if self.site_inherited and (not self.site_id or not self.site_source_evidence_ids):
            raise ValueError('inherited site requires source evidence')
        return self


class Company(Model):
    company_id: ID
    name: Fact[str] = Field(default_factory=Fact[str])
    business_number: Fact[str] = Field(default_factory=Fact[str])
    contact_name: Fact[str] = Field(default_factory=Fact[str])
    contact_phone: Fact[str] = Field(default_factory=Fact[str])
    registration_verification: Literal['not_checked', 'checked'] = 'not_checked'


class Site(Model):
    site_id: ID
    company_id: ID
    address: Fact[str] = Field(default_factory=Fact[str])


class CasePublished(Model):
    event_id: ID
    case_id: ID
    from_version: int = Field(ge=0)
    to_version: int = Field(ge=1)
    changed_fields: list[str]
    changed_evidence_ids: list[ID]
    reason: str
    published_at: datetime

    @model_validator(mode='after')
    def consecutive(self):
        if self.to_version != self.from_version + 1:
            raise ValueError('version must advance exactly once')
        return self


class ExtractionCandidate(Model):
    field_path: str
    raw_value: str
    normalized_candidate: str | None = None
    evidence_ref: str
    warnings: list[str] = Field(default_factory=list)


class Handoff(Model):
    snapshot: WasteCase
    published_event: CasePublished
    reviewed_by: ID
    reviewed_at: datetime
    original_file_ids: list[ID] = Field(default_factory=list)
    schema_version: Literal['2.0'] = '2.0'
    extractor_version: str
    rule_version: str

    @model_validator(mode='after')
    def publication(self):
        s, e = self.snapshot, self.published_event
        if s.case_version < 1 or e.case_id != s.case_id or e.to_version != s.case_version:
            raise ValueError('matching published snapshot/event required')
        if self.extractor_version != s.extractor_version or self.rule_version != s.rule_version:
            raise ValueError('version metadata mismatch')
        if not self.reviewed_by.strip():
            raise ValueError('reviewer required')
        if any(c.status == 'open' for c in s.conflicts):
            raise ValueError('M1 conservatively blocks unresolved conflicts')
        if set(e.changed_evidence_ids) - {x.evidence_id for x in s.evidence}:
            raise ValueError('event references missing evidence')
        if {x.file_id for x in s.evidence if x.file_id} - set(self.original_file_ids):
            raise ValueError('missing private source file references')
        return self
