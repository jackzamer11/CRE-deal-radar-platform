"""
Deal Radar OS — Scoring Model
==============================
Combines the four signal categories into a single 0-100 deal score,
assigns deal type, confidence level, and priority tier.

DEAL TYPE → WEIGHT PROFILE:
  PRE_MARKET      Prediction 40% | Owner 35% | Mispricing 10% | Tenant 15%
  ACTIVE_MISPRICED Prediction 20% | Owner 25% | Mispricing 35% | Tenant 20%
  TENANT_DRIVEN   Prediction 20% | Owner 20% | Mispricing 10% | Tenant 50%

PRIORITY TIERS:
  IMMEDIATE  score >= 75 AND confidence == HIGH
  HIGH       score >= 62
  WORKABLE   score >= 42
  IGNORE     score < 42
"""

from typing import Optional


def determine_deal_type(
    is_listed: bool,
    mispricing_composite: float,
    tenant_composite: float,
) -> str:
    """
    Deal type is determined by which signal category dominates.
    The system classifies each opportunity by the most actionable lens.
    """
    if is_listed and mispricing_composite >= 55:
        return "ACTIVE_MISPRICED"
    if tenant_composite >= 55:
        return "TENANT_DRIVEN"
    return "PRE_MARKET"


def compute_deal_score(
    prediction_composite: float,
    owner_behavior_composite: float,
    mispricing_composite: float,
    tenant_composite: float,
    deal_type: str,
) -> float:
    """
    Weighted composite score based on deal type profile.

    Each deal type applies different weights to surface the most
    executable form of the opportunity.
    """
    weights = {
        "PRE_MARKET": {
            "prediction": 0.40,
            "owner":      0.35,
            "mispricing": 0.10,
            "tenant":     0.15,
        },
        "ACTIVE_MISPRICED": {
            "prediction": 0.20,
            "owner":      0.25,
            "mispricing": 0.35,
            "tenant":     0.20,
        },
        "TENANT_DRIVEN": {
            "prediction": 0.20,
            "owner":      0.20,
            "mispricing": 0.10,
            "tenant":     0.50,
        },
    }

    w = weights.get(deal_type, weights["PRE_MARKET"])
    score = (
        prediction_composite * w["prediction"] +
        owner_behavior_composite * w["owner"] +
        mispricing_composite * w["mispricing"] +
        tenant_composite * w["tenant"]
    )
    return round(min(100.0, max(0.0, score)), 1)


def compute_confidence(
    score: float,
    prediction: float,
    owner_behavior: float,
    mispricing: float,
    tenant: float,
    deal_type: str,
) -> str:
    """
    Confidence level requires both a high composite score AND
    at least 2 individual signal categories above 65.

    This prevents single-signal 'false positives' — e.g. a building
    owned for 15 years but with 5% vacancy and stable rents.
    """
    relevant = [prediction, owner_behavior]
    if deal_type in ("ACTIVE_MISPRICED",):
        relevant.append(mispricing)
    if deal_type in ("TENANT_DRIVEN",):
        relevant.append(tenant)

    high_count = sum(1 for v in relevant if v >= 65)

    if score >= 72 and high_count >= 2:
        return "HIGH"
    if score >= 55:
        return "MEDIUM"
    return "LOW"


def compute_priority(score: float, confidence: str) -> str:
    if score >= 75 and confidence == "HIGH":
        return "IMMEDIATE"
    if score >= 62:
        return "HIGH"
    if score >= 42:
        return "WORKABLE"
    return "IGNORE"


def score_property(
    prediction_composite: float,
    owner_behavior_composite: float,
    mispricing_composite: float,
    tenant_composite: float,
    is_listed: bool,
) -> dict:
    """
    Full scoring pipeline for a property, optionally with linked tenant.

    Returns:
      score            — 0-100 final score
      deal_type        — PRE_MARKET / ACTIVE_MISPRICED / TENANT_DRIVEN
      confidence_level — HIGH / MEDIUM / LOW
      priority         — IMMEDIATE / HIGH / WORKABLE / IGNORE
    """
    deal_type = determine_deal_type(is_listed, mispricing_composite, tenant_composite)
    score = compute_deal_score(
        prediction_composite,
        owner_behavior_composite,
        mispricing_composite,
        tenant_composite,
        deal_type,
    )
    confidence = compute_confidence(
        score, prediction_composite, owner_behavior_composite,
        mispricing_composite, tenant_composite, deal_type,
    )
    priority = compute_priority(score, confidence)

    return {
        "score":            score,
        "deal_type":        deal_type,
        "confidence_level": confidence,
        "priority":         priority,
    }
