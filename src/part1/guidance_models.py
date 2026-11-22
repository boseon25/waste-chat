"""Additive guidance-card contract (not a law/eligibility decision)."""
from models import Model


class OfficialRoute(Model):
    source_id: str
    url: str
    checked_at: str
    checked_scope: str
    limitation: str


class GuidanceCard(Model):
    guidance_id: str
    title: str
    fields: list[str]
    reason: str
    question: str
    where: list[str]
    prepare: list[str]
    alternative: str
    caution: str
    official_routes: list[OfficialRoute]
