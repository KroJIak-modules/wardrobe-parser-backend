from app.services.settings.weight_rule_matcher import WeightRuleMatcherEntry, WeightRuleMatcherField, keyword_matches, resolve_match, resolve_match_for_fields


def test_keyword_matches_respects_word_boundaries() -> None:
    haystack = "real de catorce code 329 26 blue labradorite"

    assert keyword_matches(haystack=haystack, keyword="labradorite")
    assert not keyword_matches(haystack=haystack, keyword="bra")
    assert not keyword_matches(haystack="shoelace locks", keyword="shoe")
    assert not keyword_matches(haystack="big baggy denim", keyword="bag")
    assert not keyword_matches(haystack="coated black denim", keyword="coat")
    assert not keyword_matches(haystack="oyster perpetual 34 pink dial", keyword="pin")


def test_keyword_matches_supports_wildcards_without_losing_boundaries() -> None:
    assert keyword_matches(haystack="slate collar bikerjacket", keyword="biker*")
    assert keyword_matches(haystack="black leather lighter holder n 12", keyword="lighter hold*")
    assert not keyword_matches(haystack="highlighter holder", keyword="lighter hold*")


def test_resolve_match_prefers_more_hits_then_more_specific_keyword() -> None:
    rules = [
        WeightRuleMatcherEntry(rule_id=1, weight_grams=240, keywords=["hat", "cap"]),
        WeightRuleMatcherEntry(rule_id=2, weight_grams=190, keywords=["lighter holder", "leather lighter holder"]),
    ]

    match = resolve_match(rules=rules, haystack="black leather lighter holder n 12")

    assert match.rule_id == 2
    assert match.weight_grams == 190.0
    assert match.matched_keyword == "leather lighter holder"


def test_resolve_match_does_not_pick_false_positive_rule() -> None:
    rules = [
        WeightRuleMatcherEntry(rule_id=1, weight_grams=300, keywords=["bra"]),
        WeightRuleMatcherEntry(rule_id=2, weight_grams=130, keywords=["real de catorce"]),
    ]

    match = resolve_match(
        rules=rules,
        haystack="Real de Catorce: Code 329.26 (Blue Labradorite)",
    )

    assert match.rule_id == 2
    assert match.weight_grams == 130.0
    assert match.matched_keyword == "real de catorce"


def test_resolve_match_for_fields_prefers_title_over_description_cross_mentions() -> None:
    rules = [
        WeightRuleMatcherEntry(rule_id=13, weight_grams=1120, keywords=["jacket"]),
        WeightRuleMatcherEntry(rule_id=16, weight_grams=1860, keywords=["boot", "boots"]),
    ]

    match = resolve_match_for_fields(
        rules=rules,
        fields=[
            WeightRuleMatcherField(name="title", text="Cross Zip Boots", weight=8),
            WeightRuleMatcherField(
                name="description_text",
                text="The footwear mirrors the boldness of the Cross Zip jacket.",
                weight=1,
            ),
        ],
    )

    assert match.rule_id == 16
    assert match.weight_grams == 1860.0
    assert match.matched_keyword == "boots"


def test_resolve_match_for_fields_prefers_title_product_type_over_related_outfit_text() -> None:
    rules = [
        WeightRuleMatcherEntry(rule_id=9, weight_grams=680, keywords=["pants"]),
        WeightRuleMatcherEntry(rule_id=13, weight_grams=1120, keywords=["outerwear"]),
    ]

    match = resolve_match_for_fields(
        rules=rules,
        fields=[
            WeightRuleMatcherField(name="title", text="Desert Pants Neo", weight=8),
            WeightRuleMatcherField(
                name="description_text",
                text="Pairs seamlessly with Desert Neo outerwear for a unified silhouette.",
                weight=1,
            ),
        ],
    )

    assert match.rule_id == 9
    assert match.weight_grams == 680.0
    assert match.matched_keyword == "pants"
