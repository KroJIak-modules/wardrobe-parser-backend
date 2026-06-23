from __future__ import annotations

import re


def normalize_designer_text(value: object | None) -> str:
    return " ".join(str(value or "").strip().split())


def slugify_designer_name(raw: str) -> str:
    translit = str.maketrans(
        {
            "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
            "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
            "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
            "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
        }
    )
    value = str(raw or "").strip().lower().translate(translit)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:255] or "designer"
