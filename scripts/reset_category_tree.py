"""Reset parser category tree to the current storefront structure."""

from __future__ import annotations

from collections import OrderedDict

from sqlalchemy import delete, func

from app.core.database import SessionLocal
from app.models import ParserCategory, ParserCategoryKeyword, ParserProduct
from app.services.catalog.category_tree_rules import slugify


NOVINKI_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Футболки и лонгсливы": [
        "t-shirt",
        "t shirts",
        "tee",
        "tees",
        "top",
        "tops",
        "long sleeve",
        "longsleeve",
        "лонгслив",
        "футболк",
    ],
    "Свитшоты и худи": [
        "hoodie",
        "hoodies",
        "sweatshirt",
        "sweatshirts",
        "свитшот",
        "худи",
    ],
    "Джинсы и штаны": [
        "jeans",
        "pants",
        "trousers",
        "denim",
        "джинс",
        "штаны",
        "брюк",
    ],
    "Кроссовки и кеды": [
        "sneaker",
        "sneakers",
        "кроссов",
        "кеды",
    ],
    "Ремни": [
        "belt",
        "belts",
        "ремень",
        "ремни",
    ],
    "Украшения": [
        "jewelry",
        "jewellery",
        "ring",
        "rings",
        "necklace",
        "necklaces",
        "earring",
        "earrings",
        "bracelet",
        "bracelets",
        "украш",
        "кольц",
        "серьг",
        "подвес",
    ],
    "Сумки": [
        "bag",
        "bags",
        "handbag",
        "handbags",
        "backpack",
        "backpacks",
        "сумк",
        "рюкзак",
    ],
    "Шорты и юбки": [
        "shorts",
        "short",
        "skirt",
        "skirts",
        "шорт",
        "юбк",
    ],
    "Головные уборы": [
        "hat",
        "hats",
        "cap",
        "caps",
        "beanie",
        "headwear",
        "головн",
        "кепк",
        "шапк",
    ],
}


def _normalize_keyword(value: str) -> str:
    return value.strip().lower()


def _create_category(
    *,
    db,
    existing_slugs: set[str],
    name: str,
    slug_base: str,
    parent_id: int | None = None,
    is_fallback: bool = False,
    is_favorite: bool = False,
    is_enabled: bool = True,
) -> ParserCategory:
    base = slugify(slug_base) if slug_base else "category"
    if not base:
        base = "category"
    slug = base
    suffix = 2
    while slug in existing_slugs:
        slug = f"{base}-{suffix}"
        suffix += 1
    category = ParserCategory(
        name=name,
        slug=slug,
        parent_id=parent_id,
        is_fallback=is_fallback,
        is_favorite=is_favorite,
        is_enabled=is_enabled,
        deleted_at=None,
    )
    db.add(category)
    db.flush()
    existing_slugs.add(slug)
    return category


def _add_keywords(*, db, category_id: int, keywords: list[str]) -> None:
    unique = OrderedDict()
    for raw in keywords:
        normalized = _normalize_keyword(raw)
        if normalized:
            unique[normalized] = True
    for keyword in unique.keys():
        db.add(ParserCategoryKeyword(category_id=category_id, keyword=keyword))


def _load_brands(db) -> list[str]:
    rows = (
        db.query(ParserProduct.vendor)
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProduct.vendor.isnot(None))
        .order_by(func.lower(ParserProduct.vendor))
        .all()
    )
    unique: OrderedDict[str, str] = OrderedDict()
    for (vendor_raw,) in rows:
        name = str(vendor_raw or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in unique:
            continue
        unique[key] = name
    return list(unique.values())


def main() -> None:
    db = SessionLocal()
    try:
        non_system_ids = [
            item[0]
            for item in db.query(ParserCategory.id)
            .filter(ParserCategory.is_fallback.is_(False))
            .filter(ParserCategory.is_favorite.is_(False))
            .all()
        ]

        if non_system_ids:
            db.execute(delete(ParserCategoryKeyword).where(ParserCategoryKeyword.category_id.in_(non_system_ids)))
            db.execute(delete(ParserCategory).where(ParserCategory.id.in_(non_system_ids)))
            db.flush()

        fallback = db.query(ParserCategory).filter(ParserCategory.is_fallback.is_(True)).first()
        if fallback is None:
            fallback = _create_category(
                db=db,
                existing_slugs=set(),
                name="Прочее",
                slug_base="prochee",
                is_fallback=True,
            )
        else:
            fallback.name = "Прочее"
            fallback.is_enabled = True
            fallback.deleted_at = None

        favorite = db.query(ParserCategory).filter(ParserCategory.is_favorite.is_(True)).first()
        if favorite is None:
            existing_slugs = {item[0] for item in db.query(ParserCategory.slug).all()}
            favorite = _create_category(
                db=db,
                existing_slugs=existing_slugs,
                name="Избранное",
                slug_base="izbrannoe",
                is_favorite=True,
            )
        else:
            favorite.name = "Избранное"
            favorite.is_enabled = True
            favorite.deleted_at = None

        existing_slugs = {item[0] for item in db.query(ParserCategory.slug).all()}

        novinki = _create_category(db=db, existing_slugs=existing_slugs, name="Новинки", slug_base="novinki")
        designers = _create_category(db=db, existing_slugs=existing_slugs, name="Дизайнеры", slug_base="dizaynery")
        men = _create_category(db=db, existing_slugs=existing_slugs, name="Мужское", slug_base="muzhskoe")
        women = _create_category(db=db, existing_slugs=existing_slugs, name="Женское", slug_base="zhenskoe")
        discounts = _create_category(db=db, existing_slugs=existing_slugs, name="Скидки", slug_base="skidki")

        _add_keywords(
            db=db,
            category_id=discounts.id,
            keywords=["sale", "discount", "outlet", "final sale", "скидк", "распродаж"],
        )

        collections = _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Коллекции",
            slug_base="novinki-kollektsii",
            parent_id=novinki.id,
        )
        _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Новые поступления",
            slug_base="new-arrivals",
            parent_id=collections.id,
        )
        _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="В наличии",
            slug_base="in-stock",
            parent_id=collections.id,
        )
        _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Под заказ",
            slug_base="pre-order",
            parent_id=collections.id,
        )
        _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Мой выбор",
            slug_base="my-choice",
            parent_id=collections.id,
        )
        _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Все товары",
            slug_base="all-items",
            parent_id=collections.id,
        )

        novinki_categories = _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Категории",
            slug_base="novinki-kategorii",
            parent_id=novinki.id,
        )
        novinki_category_names = [
            "Футболки и лонгсливы",
            "Свитшоты и худи",
            "Джинсы и штаны",
            "Кроссовки и кеды",
            "Ремни",
            "Украшения",
            "Сумки",
            "Шорты и юбки",
            "Головные уборы",
        ]
        novinki_leaf_nodes: dict[str, ParserCategory] = {}
        for item in novinki_category_names:
            node = _create_category(
                db=db,
                existing_slugs=existing_slugs,
                name=item,
                slug_base=f"novinki-{item}",
                parent_id=novinki_categories.id,
            )
            novinki_leaf_nodes[item] = node

        for name, keywords in NOVINKI_CATEGORY_KEYWORDS.items():
            node = novinki_leaf_nodes.get(name)
            if node is not None:
                _add_keywords(db=db, category_id=node.id, keywords=keywords)

        men_clothes = _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Одежда",
            slug_base="men-clothes",
            parent_id=men.id,
        )
        men_shoes = _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Обувь",
            slug_base="men-shoes",
            parent_id=men.id,
        )
        men_accessories = _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Аксессуары",
            slug_base="men-accessories",
            parent_id=men.id,
        )
        for item in [
            "Верх",
            "Футболки и лонгсливы",
            "Рубашки и поло",
            "Свитшоты и худи",
            "Верхняя одежда",
            "Низ",
            "Джинсы и штаны",
            "Шорты",
        ]:
            _create_category(
                db=db,
                existing_slugs=existing_slugs,
                name=item,
                slug_base=f"men-{item}",
                parent_id=men_clothes.id,
            )
        for item in ["Кроссовки и кеды", "Ботинки и сапоги"]:
            _create_category(
                db=db,
                existing_slugs=existing_slugs,
                name=item,
                slug_base=f"men-{item}",
                parent_id=men_shoes.id,
            )
        for item in ["Украшения", "Сумки", "Ремни", "Головные уборы", "Очки", "Другое"]:
            _create_category(
                db=db,
                existing_slugs=existing_slugs,
                name=item,
                slug_base=f"men-{item}",
                parent_id=men_accessories.id,
            )

        women_clothes = _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Одежда",
            slug_base="women-clothes",
            parent_id=women.id,
        )
        women_shoes = _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Обувь",
            slug_base="women-shoes",
            parent_id=women.id,
        )
        women_accessories = _create_category(
            db=db,
            existing_slugs=existing_slugs,
            name="Аксессуары",
            slug_base="women-accessories",
            parent_id=women.id,
        )
        for item in [
            "Верх",
            "Футболки и топы",
            "Рубашки и блузы",
            "Свитшоты и худи",
            "Платья",
            "Верхняя одежда",
            "Низ",
            "Джинсы и штаны",
            "Шорты и юбки",
        ]:
            _create_category(
                db=db,
                existing_slugs=existing_slugs,
                name=item,
                slug_base=f"women-{item}",
                parent_id=women_clothes.id,
            )
        for item in ["Кроссовки и кеды", "Ботинки и сапоги", "Туфли"]:
            _create_category(
                db=db,
                existing_slugs=existing_slugs,
                name=item,
                slug_base=f"women-{item}",
                parent_id=women_shoes.id,
            )
        for item in ["Украшения", "Сумки", "Ремни", "Головные уборы", "Очки", "Другое"]:
            _create_category(
                db=db,
                existing_slugs=existing_slugs,
                name=item,
                slug_base=f"women-{item}",
                parent_id=women_accessories.id,
            )

        brands = _load_brands(db)
        for brand_key in brands:
            display_name = brand_key
            _create_category(
                db=db,
                existing_slugs=existing_slugs,
                name=display_name,
                slug_base=f"designer-{display_name}",
                parent_id=designers.id,
            )

        db.commit()
        print(f"Category tree reset done. Brands added: {len(brands)}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
