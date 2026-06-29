from __future__ import annotations

import re
import unicodedata


class ProductTitleService:
    @classmethod
    def display_title(
        cls,
        *,
        source_title: str | None,
        source_designer_name: str | None,
        source_category_name: str | None = None,
    ) -> str | None:
        title = cls._collapse_spaces(source_title)
        if not title:
            return None
        designer = cls._collapse_spaces(source_designer_name)
        if not designer:
            return title
        category = cls._collapse_spaces(source_category_name)
        if category and cls._normalize_compare(title) == cls._normalize_compare(designer):
            return category

        prefix_pattern = cls._designer_prefix_pattern(designer)
        if prefix_pattern is None:
            return title

        match = prefix_pattern.match(title)
        if match is None:
            return title

        remainder = cls._collapse_spaces(re.sub(r"^[\s\-\|:/\\,.;_]+", "", title[match.end() :]))
        if not remainder:
            return title
        if not re.search(r"\w", remainder, flags=re.UNICODE):
            return title
        if cls._normalize_compare(remainder) == cls._normalize_compare(designer):
            return title
        return remainder

    @staticmethod
    def _collapse_spaces(value: str | None) -> str:
        return " ".join(str(value or "").strip().split())

    @staticmethod
    def _normalize_compare(value: str | None) -> str:
        raw = str(value or "")
        parts: list[str] = []
        for raw_character in raw.casefold():
            if not raw_character.isalnum():
                continue
            parts.append(
                "".join(
                    character
                    for character in unicodedata.normalize("NFKD", raw_character)
                    if character.isalnum()
                )
            )
        text = "".join(parts)
        return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)

    @classmethod
    def _designer_prefix_pattern(cls, designer_name: str) -> re.Pattern[str] | None:
        tokens = re.findall(r"\w+", unicodedata.normalize("NFKC", designer_name), flags=re.UNICODE)
        if not tokens:
            return None
        pattern = r"^\s*" + r"[\W_]*".join(re.escape(token) for token in tokens) + r"(?:(?=\s|[\W_])|\b)"
        return re.compile(pattern, flags=re.IGNORECASE | re.UNICODE)
