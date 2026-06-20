"""Config sanity rules — the "linter".

Flags risky INI values that can quietly cause stutter or crashes. Deliberately
conservative and *advisory*: every finding explains the reasoning so a user can
judge, rather than asserting a fix. Pure functions over parsed data so they're
easy to test and extend (add a rule to INI_RULES).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Finding:
    severity: str  # "warn" | "info"
    title: str
    detail: str
    where: str = ""


def _to_int(value) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


def _rule_max_working_set(fname, section, key, value, ctx) -> Finding | None:
    """iRacing's memory cap set above the PC's installed RAM."""
    if not key.lower().startswith("maxworkingsetmb"):
        return None
    ram_mb = ctx.get("ram_mb")
    v = _to_int(value)
    if not ram_mb or v is None or v <= ram_mb:
        return None
    return Finding(
        "warn",
        f"{key} is higher than your installed RAM",
        f"It's set to {v:,} MB, but your PC has about {ram_mb:,} MB "
        f"(~{round(ram_mb / 1024)} GB) of RAM. iRacing can't use more memory than "
        f"you actually have — setting this above your RAM can cause paging and "
        f"stutter. Setting it at or below your RAM is safer.",
        f"{fname} · [{section}] {key}",
    )


# Each rule: fn(fname, section, key, value, ctx) -> Finding | None
INI_RULES = [_rule_max_working_set]


def lint_ini(parsed_by_file: dict[str, dict[str, dict[str, str]]],
             ram_mb: int | None = None) -> list[Finding]:
    """Run the INI rule table over {file: {section: {key: value}}}."""
    ctx = {"ram_mb": ram_mb}
    out: list[Finding] = []
    for fname, sections in parsed_by_file.items():
        for section, keys in sections.items():
            for key, value in keys.items():
                for rule in INI_RULES:
                    f = rule(fname, section, key, value, ctx)
                    if f:
                        out.append(f)
    return out
