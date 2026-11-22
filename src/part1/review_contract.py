"""Shape of a DB-exported review bundle; validating it does NOT re-verify a live DB."""
from typing import Literal
from pydantic import Field, model_validator
from models import Model, Company, Site, Evidence, WasteCase
from guidance_models import GuidanceCard


class DBCheck(Model):
    submission_id: str
    status: Literal['PASS']
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    counts: dict[str, int]
    checked: int = Field(ge=0)
    unchecked: int = Field(ge=0)


class DBReviewExport(Model):
    export_kind: Literal['db_verified_review_draft'] = 'db_verified_review_draft'
    schema_version: Literal['2.0'] = '2.0'
    published: Literal[False] = False
    database_verification: DBCheck
    company: Company
    sites: list[Site]
    evidence: list[Evidence]
    cases: list[WasteCase]
    guidance_cards: list[GuidanceCard] = Field(default_factory=list)

    @model_validator(mode='after')
    def links(self):
        sites = {s.site_id: s for s in self.sites}
        evidence = {e.evidence_id: e for e in self.evidence}
        if len(sites) != len(self.sites) or len(evidence) != len(self.evidence):
            raise ValueError('duplicate site or evidence')
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError('duplicate case')
        for s in self.sites:
            if s.company_id != self.company.company_id:
                raise ValueError('site/company mismatch')
        common_facts = [self.company.name, self.company.business_number, self.company.contact_name,
                        self.company.contact_phone] + [s.address for s in self.sites]
        for fact in common_facts:
            if set(fact.evidence_ids) - set(evidence):
                raise ValueError('common fact refers to missing evidence')
        for c in self.cases:
            if c.company_id != self.company.company_id or (c.site_id and c.site_id not in sites):
                raise ValueError('case/company/site mismatch')
            if c.case_version != 0 or c.submission_id != self.database_verification.submission_id:
                raise ValueError('review export requires matching draft cases')
            for e in c.evidence:
                if e.evidence_id not in evidence or e != evidence[e.evidence_id]:
                    raise ValueError('case/envelope evidence mismatch')
        if self.database_verification.counts.get('case_candidates') != len(self.cases):
            raise ValueError('case count mismatch')
        if self.database_verification.counts.get('sites', 0) + self.database_verification.counts.get('phase2_sites', 0) != len(self.sites):
            raise ValueError('site count mismatch')
        return self
