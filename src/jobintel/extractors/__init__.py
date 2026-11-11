"""Recruitment-platform adapters and the universal router.

Each adapter knows how to talk to one underlying careers technology (Workday,
Amazon's proprietary platform, SmartRecruiters, Greenhouse, Lever). The generic
adapter covers everything else through sitemap discovery + schema.org JSON-LD.

* :func:`build_extractor` - instantiate the adapter named by a registry entry
  (what the 10-company pipeline uses).
* :func:`build_for_url` - the universal entry point: detect the platform behind
  an arbitrary careers URL and route it to the matching adapter (used by
  ``crawl.py``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

from ..detect import detect_platform, guess_company_name, platform_config
from ..normalize import slugify
from .accenture import AccentureExtractor
from .amazon import AmazonExtractor
from .ashby import AshbyExtractor
from .base import BaseExtractor, ExtractionResult
from .generic import GenericExtractor
from .greenhouse import GreenhouseExtractor
from .lever import LeverExtractor
from .smartrecruiters import SmartRecruitersExtractor
from .tcs import TCSExtractor
from .wipro import WiproExtractor
from .workable import WorkableExtractor
from .workday import WorkdayExtractor

GENERIC = "generic"

REGISTRY: Dict[str, Type[BaseExtractor]] = {
    "workday": WorkdayExtractor,
    "amazon": AmazonExtractor,
    "smartrecruiters": SmartRecruitersExtractor,
    "greenhouse": GreenhouseExtractor,
    "lever": LeverExtractor,
    "tcs": TCSExtractor,
    "wipro": WiproExtractor,
    "accenture": AccentureExtractor,
    "ashby": AshbyExtractor,
    "workable": WorkableExtractor,
    GENERIC: GenericExtractor,
}


def build_extractor(company: Dict[str, Any], **kwargs: Any) -> BaseExtractor:
    """Instantiate the adapter declared by a registry entry."""
    name = company.get("extractor", "")
    if name not in REGISTRY:
        raise KeyError(
            f"unknown extractor '{name}' for {company.get('company_name')}; "
            f"known: {sorted(REGISTRY)}"
        )
    return REGISTRY[name](company, **kwargs)


def build_for_url(
    careers_url: str,
    company_name: Optional[str] = None,
    industry: str = "",
    sitemap_url: Optional[str] = None,
    **kwargs: Any,
) -> BaseExtractor:
    """Detect the platform behind ``careers_url`` and build the right adapter.

    Known platforms use their typed adapter; anything else - including SAP
    SuccessFactors, Phenom and unknown ATSs - falls back to the generic crawler.
    """
    platform = detect_platform(careers_url)
    extractor_name = platform if platform in REGISTRY else GENERIC
    name = company_name or guess_company_name(careers_url)

    if extractor_name == GENERIC:
        config: Dict[str, Any] = {"careers_url": careers_url}
        if sitemap_url:
            config["sitemap_url"] = sitemap_url
    else:
        config = platform_config(extractor_name, careers_url)

    company = {
        "company_name": name,
        "industry": industry,
        "careers_url": careers_url,
        "extractor": extractor_name,
        "slug": slugify(name) or "company",
        "detected_platform": platform,
        "config": config,
    }
    return build_extractor(company, **kwargs)


__all__ = [
    "BaseExtractor",
    "ExtractionResult",
    "REGISTRY",
    "GENERIC",
    "build_extractor",
    "build_for_url",
    "WorkdayExtractor",
    "AmazonExtractor",
    "SmartRecruitersExtractor",
    "GreenhouseExtractor",
    "LeverExtractor",
    "TCSExtractor",
    "WiproExtractor",
    "AccentureExtractor",
    "AshbyExtractor",
    "WorkableExtractor",
    "GenericExtractor",
]
