# SPDX-License-Identifier: MIT
"""Skill matcher -- score bounties against a contributor's skill set and
recommend the best fits.
"""

from __future__ import annotations

from heapq import nlargest
from importlib import resources
import json
import os
import re
from typing import Dict, List

# Default skill tags.  Overridden at runtime if data/skill_tags.json exists.
# Supports two formats:
#   - Flat list:   {"python": ["python", "flask", ...]}
#   - Structured:  {"python": {"aliases": ["py"], "bounty_labels": ["python", "backend"]}}
SKILL_TAGS: Dict[str, List[str]] = {
    "python": ["python", "flask", "pytest", "pip", "django"],
    "rust": ["rust", "cargo", "crate"],
    "javascript": ["javascript", "node", "npm", "react", "typescript"],
    "docker": ["docker", "compose", "container", "dockerfile"],
    "ci-cd": ["ci", "cd", "github actions", "workflow", "lint"],
    "documentation": ["docs", "readme", "contributing", "translation", "guide"],
    "security": [
        "security", "audit", "vulnerability", "red team", "fuzz", "pentest",
    ],
    "social-media": ["star", "share", "tweet", "post", "upvote", "review"],
    "blockchain": [
        "blockchain", "token", "wallet", "attestation", "mining",
    ],
    "testing": ["test", "pytest", "coverage", "benchmark"],
}


def _normalise_tags(raw: Dict) -> Dict[str, List[str]]:
    """Normalise skill_tags.json into flat {skill: [keywords]} format.

    Accepts both structured (aliases + bounty_labels) and flat (list) formats.
    For structured entries, the keyword list is built from the skill name
    itself, its aliases, and its bounty_labels.  Malformed optional structured
    fields are ignored instead of making matcher import fail.
    """
    result: Dict[str, List[str]] = {}
    for skill, value in raw.items():
        if not isinstance(skill, str) or not skill.strip():
            continue
        if isinstance(value, list):
            result[skill] = value
        elif isinstance(value, dict):
            keywords = [skill]
            for field in ("aliases", "bounty_labels"):
                entries = value.get(field, [])
                if not isinstance(entries, list):
                    continue
                keywords.extend(
                    kw for kw in entries
                    if isinstance(kw, str) and kw.strip()
                )
            # Deduplicate while preserving order
            seen = set()
            unique = []
            for kw in keywords:
                kw_lower = kw.lower()
                if kw_lower not in seen:
                    seen.add(kw_lower)
                    unique.append(kw_lower)
            result[skill] = unique
        else:
            result[skill] = [skill]
    return result


def _normalise_aliases(raw: Dict) -> Dict[str, str]:
    """Resolve only explicit, unambiguous contributor aliases from the catalog.

    Bounty labels and flat keyword lists are search terms, not category aliases.
    A name declared by multiple categories stays a literal skill; canonical
    category names take precedence when preparing a request.
    """
    aliases: Dict[str, str] = {}
    ambiguous = set()
    for skill, value in raw.items():
        if not isinstance(skill, str) or not skill.strip() or not isinstance(value, dict):
            continue
        entries = value.get("aliases", [])
        if not isinstance(entries, list):
            continue
        for alias in entries:
            if not isinstance(alias, str) or not alias.strip():
                continue
            name = alias.lower()
            previous = aliases.get(name)
            if previous is not None and previous != skill:
                ambiguous.add(name)
            else:
                aliases[name] = skill
    return {name: skill for name, skill in aliases.items() if name not in ambiguous}


SKILL_ALIASES: Dict[str, str] = {}


# Source checkouts retain the authoritative data/skill_tags.json lookup.
# Distributions bundle that same file as a package resource during the build.
_DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "skill_tags.json"
)
try:
    if os.path.isfile(_DATA_FILE):
        with open(_DATA_FILE, "r", encoding="utf-8") as fh:
            _loaded = json.load(fh)
    else:
        _loaded = json.loads(
            resources.files("concierge").joinpath("skill_tags.json").read_text(
                encoding="utf-8"
            )
        )
    if isinstance(_loaded, dict):
        SKILL_TAGS = _normalise_tags(_loaded)
        SKILL_ALIASES = _normalise_aliases(_loaded)
except (json.JSONDecodeError, OSError):
    pass  # fall back to built-in defaults


def _bounty_text(bounty: dict) -> str:
    """Combine all searchable text fields from a bounty into one string."""
    labels = bounty.get("labels", [])
    if isinstance(labels, (list, tuple)):
        names = (
            label.get("name") if isinstance(label, dict) else label
            for label in labels
        )
        label_text = " ".join(
            name for name in names if isinstance(name, str)
        )
    else:
        label_text = ""

    parts = [
        value if isinstance(value, str) else ""
        for value in (
            bounty.get("title", ""),
            bounty.get("body", ""),
            label_text,
            bounty.get("difficulty", ""),
            bounty.get("language", ""),
        )
    ]
    return " ".join(parts).lower()


def _keyword_matches(text: str, keyword: str) -> bool:
    """Return whether *keyword* occurs as a complete token or phrase.

    Skill keywords are semantic labels, not arbitrary substrings.  Word
    boundaries prevent short tags such as ``ci``, ``go``, or ``rust`` from
    matching unrelated words such as ``specific``, ``django``, or
    ``trustworthy`` while still allowing punctuation-delimited labels and
    multi-word phrases.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        return False
    normalized = keyword.casefold()
    return re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", text.casefold()) is not None


_SkillPattern = tuple[List[str], re.Pattern[str]]


def _skill_patterns(skills: List[str]) -> List[_SkillPattern | None]:
    """Prepare literal prefilters and one token search per requested skill."""
    patterns = []
    for skill in skills:
        name = skill.lower()
        # Canonical categories win over aliases. Resolve against the current
        # mapping on every call so removed/replaced categories cannot be cached.
        category = name if name in SKILL_TAGS else SKILL_ALIASES.get(name, name)
        keywords = SKILL_TAGS.get(category, [name])
        normalized_keywords = [
            keyword.casefold()
            for keyword in keywords
            if isinstance(keyword, str) and keyword.strip()
        ]
        alternatives = "|".join(re.escape(keyword) for keyword in normalized_keywords)
        patterns.append(
            (normalized_keywords, re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)"))
            if normalized_keywords else None
        )
    return patterns


def _score_text(text: str, patterns: List[_SkillPattern | None]) -> float:
    """Score prepared text without repeating normalization for each keyword."""
    if not patterns or not text.strip():
        return 0.0
    normalized = text.casefold()
    # Literal presence is necessary; the boundary regex still decides a match.
    matched = sum(
        prepared is not None
        and any(keyword in normalized for keyword in prepared[0])
        and prepared[1].search(normalized) is not None
        for prepared in patterns
    )
    return matched / len(patterns)


def match_skills(bounty: dict, skills: List[str]) -> float:
    """Score how well *bounty* matches the given *skills* (0.0 -- 1.0).

    *skills* accepts category names (keys of ``SKILL_TAGS``) and explicit,
    unambiguous catalog aliases, e.g. ``["python", "qa"]``. Unknown names keep
    their literal keyword behavior.
    """
    if not skills:
        return 0.0

    text = _bounty_text(bounty)
    if not text.strip():
        return 0.0
    return _score_text(text, _skill_patterns(skills))


def recommend(
    bounties: List[dict],
    skills: List[str],
    limit: int = 10,
) -> List[dict]:
    """Return the top *limit* bounties matching *skills*, sorted by score.

    Each returned dict is the original bounty dict with an extra
    ``match_score`` key (float, 0.0--1.0). Keyword patterns are prepared once
    for this call, so later catalog changes remain visible on the next call.
    Stop once limit perfect matches determine the stable result; no later
    score can exceed 1.0. Empty skills select the all-zero input prefix.
    """
    if limit <= 0 or not bounties:
        return []

    patterns = _skill_patterns(skills)
    if not patterns:
        # Every score is zero; stable ranking is exactly the input prefix.
        return [dict(bounty, match_score=0.0) for bounty in bounties[:limit]]
    if limit >= len(bounties):
        scored_all = [
            dict(bounty, match_score=(
                _score_text(_bounty_text(bounty), patterns) if patterns else 0.0
            ))
            for bounty in bounties
        ]
        scored_all.sort(key=lambda bounty: bounty["match_score"], reverse=True)
        return scored_all

    def scored_until_full():
        full_matches = 0
        for bounty in bounties:
            score = _score_text(_bounty_text(bounty), patterns)
            yield score, bounty
            if score == 1.0:
                full_matches += 1
                # No later row can beat 1.0, and equal scores keep input order.
                if full_matches == limit:
                    break

    # nlargest keeps input order for equal scores and retains only limit rows.
    selected = nlargest(limit, scored_until_full(), key=lambda item: item[0])
    return [dict(bounty, match_score=score) for score, bounty in selected]
