"""Thai amphoe (district) → province lookup.

Source: kongvut/thai-province-data (open data, MIT-licensed). Snapshot
sits in app/data/thai_districts.json — 77 provinces, 925 districts. We
strip the "เขต/อำเภอ" prefix so users can type either form.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "thai_districts.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(_DATA_PATH.read_text())


def lookup_provinces(district: str) -> List[str]:
    """Return all candidate Thai province names for a district. Empty
    list = no match (free-text fallback). Most districts return exactly
    one province; only 'จอมทอง' (2) and 'เฉลิมพระเกียรติ' (5) need
    picker UI. Accepts bare 'นิมมาน' or prefixed 'เขตนิมมาน' forms."""
    if not district:
        return []
    return list(_load()["by_district"].get(district.strip(), []))


def lookup_province(district: str) -> Optional[str]:
    """Convenience: first match if unambiguous (1 candidate), else None.
    Use lookup_provinces() to get the full list when N>1 needs a picker."""
    matches = lookup_provinces(district)
    return matches[0] if len(matches) == 1 else None


def all_districts() -> List[str]:
    """Sorted list of district short names — used as the <datalist>
    options for the S2.1 picker autocomplete."""
    return list(_load()["districts"])
