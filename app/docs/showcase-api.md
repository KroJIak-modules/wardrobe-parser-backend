# Wardrobe Showcase API (для фронтенда витрины)

Публичный контракт витрины: чтение категорий, товаров и изображений.

- OpenAPI JSON: `/api/openapi/showcase.json`
- Swagger UI (showcase): `/api/docs/showcase`
- ReDoc: `/api/redoc/showcase`
- Markdown (скачать): `/api/docs/showcase.md`

## Базовые правила

- Базовый префикс API: `/api/v1`
- Для витрины авторизация **не требуется**
- Все цены и статусы уже нормализованы backend

## Эндпоинты

## 1) Корневые категории

`GET /api/v1/catalog/categories/roots`

Параметры:
- `include_counts` (boolean, default `true`) — добавлять количество товаров.

Пример:
```bash
curl "http://localhost:8000/api/v1/catalog/categories/roots?include_counts=1"
```

## 2) Ветка категорий корня

`GET /api/v1/catalog/categories/root/{root_slug}`

Параметры:
- `root_slug` — slug корневой категории
- `include_counts` (boolean, default `true`)

Пример:
```bash
curl "http://localhost:8000/api/v1/catalog/categories/root/muzhskoe?include_counts=1"
```

## 3) Каталог товаров витрины

`GET /api/v1/catalog/products`

Параметры:
- `category_slug` (optional)
- `search` (optional)
- `source_id` (optional)
- `status` (optional: `available` | `out_of_stock` | `hidden`)
- `limit` (1..120, default 36)
- `cursor` (optional) — курсор следующей страницы

Пример:
```bash
curl "http://localhost:8000/api/v1/catalog/products?category_slug=muzhskoe&limit=36"
```

## 4) Карточка товара

`GET /api/v1/products/{product_id}`

Пример:
```bash
curl "http://localhost:8000/api/v1/products/12345"
```

## 5) Изображение товара

`GET /api/v1/images/{image_id}`

Опциональные query-параметры:
- `w` — ширина
- `h` — высота
- `q` — качество

Пример:
```bash
curl "http://localhost:8000/api/v1/images/10?w=600&h=800&q=80"
```
