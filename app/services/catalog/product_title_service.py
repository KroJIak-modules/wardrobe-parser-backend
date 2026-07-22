from __future__ import annotations

import re
import unicodedata


class ProductTitleService:
    _MACHINE_CODE_TOKEN_RE = re.compile(r"^[A-Z0-9]+(?:[-_][A-Z0-9]+)+$", flags=re.IGNORECASE)
    _MACHINE_CODE_PREFIX_RE = re.compile(r"^[A-Z]{1,4}\d{1,4}[A-Z0-9_-]*$", flags=re.IGNORECASE)
    _TRAILING_PARENS_RE = re.compile(r"^(?P<title>.+?)\s*\((?P<value>[^()]*)\)\s*$", flags=re.DOTALL)

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

        remainder = cls._collapse_spaces(re.sub(r"^[\s\W_]+", "", title[match.end() :]))
        if not remainder:
            return title
        if not re.search(r"\w", remainder, flags=re.UNICODE):
            return title
        if cls._normalize_compare(remainder) == cls._normalize_compare(designer):
            return title
        return remainder

    @classmethod
    def public_title(
        cls,
        *,
        source_title: str | None,
        source_designer_name: str | None,
        source_category_name: str | None,
        clean: bool,
    ) -> str | None:
        title = cls.display_title(
            source_title=source_title,
            source_designer_name=source_designer_name,
            source_category_name=source_category_name,
        )
        if not clean:
            return title
        return cls._clean_public_title(title) or title

    @classmethod
    def _clean_public_title(cls, value: str | None) -> str | None:
        title = cls._collapse_spaces(value)
        if not title:
            return None
        split_title = cls._remove_machine_code_segment(title)
        without_sku = cls._remove_trailing_sku_parentheses(split_title)
        return cls._collapse_spaces(without_sku) or title

    @classmethod
    def _remove_machine_code_segment(cls, title: str) -> str:
        if title.count("|") != 1:
            return title
        left, right = (cls._collapse_spaces(part) for part in title.split("|", maxsplit=1))
        if cls._is_machine_code(left) and right:
            return right
        if cls._is_machine_code(right) and left:
            return left
        return title

    @classmethod
    def _remove_trailing_sku_parentheses(cls, title: str) -> str:
        match = cls._TRAILING_PARENS_RE.match(title)
        if match is None:
            return title
        candidate = cls._collapse_spaces(match.group("value"))
        remainder = cls._collapse_spaces(match.group("title"))
        if not remainder or not cls._is_machine_code(candidate):
            return title
        return remainder

    @classmethod
    def _is_machine_code(cls, value: str) -> bool:
        normalized = cls._collapse_spaces(value)
        if not normalized or " " in normalized:
            return False
        if cls._MACHINE_CODE_TOKEN_RE.fullmatch(normalized) is not None:
            return any(character.isdigit() for character in normalized)
        return bool(
            cls._MACHINE_CODE_PREFIX_RE.fullmatch(normalized)
            and any(character.isdigit() for character in normalized)
            and any(character.isalpha() for character in normalized)
        )

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
