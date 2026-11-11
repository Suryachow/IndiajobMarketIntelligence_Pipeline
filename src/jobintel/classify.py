"""Deterministic job-family and seniority classification.

Both classifiers are transparent, keyword-driven heuristics over the posting's
own title / category / description — no model, no external data, fully
reproducible. Titles are the strongest signal and are matched first; the
employer's own category is used as a fallback, then the description.

Derived values are convenience analytics, kept separate from the required
brief fields, and a posting that carries no usable signal is labelled
``Other`` (family) or left blank (seniority) rather than guessed.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Ordered (pattern, family) rules — first match wins, so the most specific /
# most constrained families come first and the broad "Engineering" /
# "Operations" catch-alls come near the end.
_FAMILY_RULES: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"clinical|medical|nurse|physician|pharmac|patient|drug safety|"
            r"pharmacovigilance|regulatory affairs|biostatistic|epidemiolog",
            re.I,
        ),
        "Clinical & Medical",
    ),
    (
        re.compile(r"\blegal\b|compliance|\bcounsel\b|attorney|paralegal|contract manager", re.I),
        "Legal & Compliance",
    ),
    (
        re.compile(
            r"human resources|\bhr\b|recruit|\btalent\b|people partner|"
            r"compensation (?:&|and) benefits|learning (?:&|and) development",
            re.I,
        ),
        "Human Resources",
    ),
    (
        re.compile(
            r"financ|accounting|accountant|\baudit\b|\btax\b|treasury|controller|"
            r"fp&a|credit analyst|investment|banking operations",
            re.I,
        ),
        "Finance & Accounting",
    ),
    (
        re.compile(
            r"data scien|data analyst|data engineer|data architect|data science|"
            r"\banalytics\b|machine learning|\bml\b|business intelligence|\bbi\b|"
            r"statistician|data warehouse|\betl\b",
            re.I,
        ),
        "Data & Analytics",
    ),
    (
        re.compile(r"design|\bux\b|\bui\b|user experience|interaction|visual|graphic", re.I),
        "Design",
    ),
    (
        re.compile(r"product manager|product owner|product management|product analyst|\bproduct\b", re.I),
        "Product",
    ),
    (
        re.compile(
            r"marketing|\bbrand\b|\bseo\b|\bsem\b|content|social media|growth|"
            r"campaign|communications|public relations|\bpr\b",
            re.I,
        ),
        "Marketing",
    ),
    (
        re.compile(
            r"\bsales\b|account executive|account manager|business development|\bbd\b|"
            r"pre-?sales|relationship manager|territory|channel",
            re.I,
        ),
        "Sales",
    ),
    (
        re.compile(
            r"customer support|customer service|customer success|technical support|"
            r"support engineer|call cent(?:er|re)|\bbpo\b|service desk|help ?desk|"
            r"customer care|customer experience",
            re.I,
        ),
        "Customer Support",
    ),
    (
        re.compile(
            r"supply chain|logistic|procure|sourcing|warehouse|inventory|"
            r"distribution|fulfil?lment|fleet",
            re.I,
        ),
        "Supply Chain & Logistics",
    ),
    (
        re.compile(r"quality assurance|quality control|\bqa\b|\bqc\b|quality engineer|quality analyst|validation", re.I),
        "Quality",
    ),
    (
        re.compile(r"security|infosec|cyber|\bsoc\b|penetration", re.I),
        "Security",
    ),
    (
        re.compile(
            r"\bit\b|system administrator|sysadmin|network|database administrator|\bdba\b|"
            r"infrastructure|cloud|devops|site reliability|\bsre\b|platform engineer|"
            r"desktop support|technical operations",
            re.I,
        ),
        "IT & Infrastructure",
    ),
    (
        re.compile(
            r"engineer|engineering|developer|software|\bsde\b|programmer|architect|"
            r"full ?stack|back ?end|front ?end|mobile|android|\bios\b|embedded|firmware|"
            r"automation|technical",
            re.I,
        ),
        "Engineering",
    ),
    (
        re.compile(
            r"operations|\bops\b|process|production|manufactur|plant|maintenance|"
            r"facilit|site operations|coordinator|\bfacilities\b",
            re.I,
        ),
        "Operations",
    ),
    (
        re.compile(r"research|\br&d\b|scientist|\blab\b|laboratory", re.I),
        "Research",
    ),
    (
        re.compile(r"administrative|\badmin\b|executive assistant|office manager|receptionist", re.I),
        "Administrative",
    ),
]

# Ordered seniority rules — first match wins. Director/Executive are checked
# before Manager so that e.g. "Associate Director" and "Senior Manager" resolve
# correctly.
_SENIORITY_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bchief\b|\bc[teof]o\b|\bpresident\b|\bowner\b|\bfounder\b", re.I), "Executive"),
    (re.compile(r"\bdirector\b|\bhead of\b|\bhead,|\bvp\b|\bvice president\b|\bpartner\b", re.I), "Director"),
    (re.compile(r"manager|supervisor|management|\bsuperintendent\b", re.I), "Manager"),
    (re.compile(r"\blead\b|\bprincipal\b|\bstaff\b|architect|expert|distinguished", re.I), "Lead"),
    (re.compile(r"\bsenior\b|\bsr\.?\b|\bsme\b", re.I), "Senior"),
    (
        re.compile(r"entry level|\bjunior\b|\bjr\.?\b|fresher|graduate|\bassociate\b|\btrainee\b", re.I),
        "Entry",
    ),
    (re.compile(r"intern|apprentice", re.I), "Internship"),
]


def _first_match(haystack: str, rules: List[Tuple[re.Pattern, str]]) -> Optional[str]:
    for pattern, label in rules:
        if pattern.search(haystack):
            return label
    return None


def job_family(title: str, category: str = "", description: str = "") -> str:
    """Return the standardized job family for a posting (``Other`` if unknown)."""
    for haystack in (title, category):
        if haystack and haystack.strip():
            found = _first_match(haystack, _FAMILY_RULES)
            if found:
                return found
    if description:
        found = _first_match(description[:400], _FAMILY_RULES)
        if found:
            return found
    return "Other"


def _years_from_experience(experience: str) -> Optional[int]:
    match = re.search(r"(\d{1,2})\s*(?:\+|-|to|–)?\s*(?:\d{1,2})?\s*(?:years?|yrs?)", experience or "", re.I)
    if match:
        return int(match.group(1))
    return None


def seniority_level(title: str, description: str = "", experience: str = "") -> str:
    """Return the standardized seniority level (``''`` when no signal exists).

    The title is authoritative; the experience string is only consulted as a
    fallback so that a bare ``"Engineer"`` with a stated "8+ years" is graded
    ``Senior`` rather than left blank.
    """
    if title:
        found = _first_match(title, _SENIORITY_RULES)
        if found:
            return found
    years = _years_from_experience(experience)
    if years is None and description:
        years = _years_from_experience(description[:400])
    if years is not None:
        if years >= 8:
            return "Senior"
        if years >= 3:
            return "Mid"
        return "Entry"
    return ""


def classify_posting(posting) -> None:
    """Fill ``job_family`` / ``seniority_level`` on a :class:`JobPosting` in place."""
    posting.job_family = job_family(
        posting.job_title, posting.job_category, posting.job_description
    )
    posting.seniority_level = seniority_level(
        posting.job_title, posting.job_description, posting.experience_required
    )
