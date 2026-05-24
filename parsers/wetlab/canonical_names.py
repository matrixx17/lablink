"""
Canonical parameter-name mapping for wet lab data.

Bioreactor controllers, offline analytics, and the QC engine all need to
agree on parameter names. The same physical quantity shows up as "pH",
"pH [-]", or "ph" depending on the export; we collapse all of these to
canonical snake_case names so downstream code (QC, methods export,
dashboard charts) can look up the same series consistently.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# Order matters: longer/more-specific aliases come first so e.g.
# "viable_cell_density" matches before generic "viable".
_ALIAS_MAP: List[Tuple[str, str]] = [
    # pH
    ("ph", "ph"),
    # Dissolved oxygen
    ("dissolved_oxygen", "do_percent"),
    ("dissolved oxygen", "do_percent"),
    ("do_percent", "do_percent"),
    ("do%", "do_percent"),
    ("do", "do_percent"),
    ("po2", "do_percent"),
    # Temperature
    ("temperature_c", "temperature_c"),
    ("temperature", "temperature_c"),
    ("temp", "temperature_c"),
    ("t", "temperature_c"),  # very weak — only used after unit/context filter
    # Agitation
    ("agitation_rpm", "agitation_rpm"),
    ("agitation", "agitation_rpm"),
    ("stir", "agitation_rpm"),
    ("agit", "agitation_rpm"),
    ("rpm", "agitation_rpm"),
    # Feed rate
    ("feed_rate", "feed_rate_ml_per_h"),
    ("f_glucose", "feed_rate_ml_per_h"),
    ("feed", "feed_rate_ml_per_h"),
    # Volume
    ("volume_l", "volume_l"),
    ("volume", "volume_l"),
    ("vol", "volume_l"),
    ("v", "volume_l"),
    # Cell density
    ("viable_cell_density", "vcd_e6_per_ml"),
    ("viable_cell", "vcd_e6_per_ml"),
    ("viable cells", "vcd_e6_per_ml"),
    ("vcd_e6_per_ml", "vcd_e6_per_ml"),
    ("vcd", "vcd_e6_per_ml"),
    ("cells_per_ml", "vcd_e6_per_ml"),
    ("cells/ml", "vcd_e6_per_ml"),
    # Viability
    ("viability_percent", "viability_percent"),
    ("viability", "viability_percent"),
    ("viab%", "viability_percent"),
    ("viab", "viability_percent"),
    # Glucose
    ("glucose_g_per_l", "glucose_g_per_l"),
    ("glucose", "glucose_g_per_l"),
    ("glc", "glucose_g_per_l"),
    # Lactate
    ("lactate_g_per_l", "lactate_g_per_l"),
    ("lactate", "lactate_g_per_l"),
    ("lac", "lactate_g_per_l"),
    # Titer / product
    ("titer_mg_per_l", "titer_mg_per_l"),
    ("titer", "titer_mg_per_l"),
    ("igg", "titer_mg_per_l"),
    ("product", "titer_mg_per_l"),
    # Osmolality
    ("osmolality_mosm", "osmolality_mosm"),
    ("osmolality", "osmolality_mosm"),
    ("osm", "osmolality_mosm"),
]


# Header parts we strip before alias matching — units, brackets, common decorations.
_UNIT_PATTERN = re.compile(
    r"""
    \s*[\[(]              # opening bracket or paren
    [^\])]*               # anything inside (units, %, °C, etc.)
    [\])]                 # closing bracket
    |
    \s*\#\d+              # vessel/channel suffix like "#3"
    """,
    re.VERBOSE,
)


def _strip_units(header: str) -> str:
    """Remove bracketed-unit suffixes and trailing whitespace."""
    s = _UNIT_PATTERN.sub("", header)
    return s.strip()


def _slugify(header: str) -> str:
    """Lowercase, collapse non-alphanumerics to a single underscore."""
    s = header.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s


def extract_unit(header: str) -> Optional[str]:
    """
    Pull the unit string from a header like 'DO [%]' or 'Temp (°C)'.

    Returns None if no bracketed unit is found.
    """
    m = re.search(r"[\[(]\s*([^\])]+?)\s*[\])]", header)
    if not m:
        return None
    unit = m.group(1).strip()
    # Strip placeholder units some vendors use to mean "dimensionless"
    if unit in ("-", "_", ""):
        return None
    return unit


def canonicalize_parameter(header: str) -> Optional[str]:
    """
    Map a CSV column header to a canonical wet-lab parameter name.

    Returns None if the header doesn't match any known alias. Matching is:
    1. Strip units in brackets and parentheses.
    2. Slugify to lowercase snake_case.
    3. Walk the alias table in order; first containment hit wins.

    The walk-in-order approach lets longer aliases match before short
    ones (e.g. "viable_cell_density" before "viable_cell" before "cells").

    Examples:
        "DO [%]"               -> "do_percent"
        "Temp (°C)"            -> "temperature_c"
        "Viable Cells/mL"      -> "vcd_e6_per_ml"
        "pH"                   -> "ph"
        "Sample Time (h)"      -> None   # not a measurement parameter
    """
    if not header:
        return None
    bare = _strip_units(header)
    slug = _slugify(bare)
    if not slug:
        return None

    # Exact-match pass first
    for alias, canonical in _ALIAS_MAP:
        if slug == _slugify(alias):
            return canonical

    # Substring / boundary-aware contains pass
    for alias, canonical in _ALIAS_MAP:
        token = _slugify(alias)
        if not token:
            continue
        # Require token to appear as a word piece in the slug
        if token == slug or slug.startswith(token + "_") or slug.endswith("_" + token) \
                or ("_" + token + "_") in ("_" + slug + "_"):
            return canonical

    return None


# Time-axis aliases. Used by generic parsers to find the timestamp column.
_TIME_ALIASES: set = {
    "time", "t", "elapsed", "elapsed_time", "elapsed time", "timestamp",
    "datetime", "date", "date_time", "time_h", "time h", "time_min",
}


def is_time_column(header: str) -> bool:
    """True if this header looks like the timestamp axis."""
    if not header:
        return False
    bare = _strip_units(header)
    slug = _slugify(bare)
    if slug in _TIME_ALIASES:
        return True
    # Heuristic: starts with "time" or "elapsed" or "t_", or contains
    # "_time" / "time_" (covers "sample_time", "time_h", "time_min").
    return (
        slug.startswith(("time", "elapsed", "t_"))
        or slug.endswith(("_time", "_time_h", "_time_min"))
        or "time_" in slug
        or "_time" in slug
    )


# Unit suffix → canonical unit string. Useful when the alias doesn't carry
# an obvious unit (e.g. canonical "glucose_g_per_l" implies g/L) but the
# input header has something different ("Glucose [mg/dL]") and we want to
# warn or normalise. For v1 we just return what the header reported.
def derive_unit_for(canonical_name: str, header: str) -> Optional[str]:
    """Choose the best unit string for a canonical parameter."""
    explicit = extract_unit(header)
    if explicit:
        return explicit
    # Sensible defaults based on canonical name
    defaults: Dict[str, str] = {
        "ph": "pH",
        "do_percent": "%",
        "temperature_c": "°C",
        "agitation_rpm": "rpm",
        "feed_rate_ml_per_h": "mL/h",
        "volume_l": "L",
        "vcd_e6_per_ml": "10^6 cells/mL",
        "viability_percent": "%",
        "glucose_g_per_l": "g/L",
        "lactate_g_per_l": "g/L",
        "titer_mg_per_l": "mg/L",
        "osmolality_mosm": "mOsm/kg",
    }
    return defaults.get(canonical_name)
