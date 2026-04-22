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

## Админ: медиа витрины (для frontend админки)

Эти ручки используются в разделе `Настройки -> Медиа витрины`.

- Требуют админ-авторизацию (`Authorization: Bearer <token>`).
- Базовый префикс: `/api/v1/settings`.

### 6) Получить текущее состояние медиа витрины

`GET /api/v1/settings/showcase-media`

Ответ:
- `showcase_hero_image_asset_id` — id заставки или `null`;
- `showcase_carousel_image_asset_ids` — массив id слайдов карусели;
- `carousel_limit` — максимальное число слайдов (сейчас `20`).

Пример:
```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/api/v1/settings/showcase-media"
```

### 7) Обновить медиа витрины

`PATCH /api/v1/settings/showcase-media`

Тело запроса:
- `showcase_hero_image_asset_id` — `number | null`;
- `showcase_carousel_image_asset_ids` — `number[]`.

Поведение:
- карусель сохраняется в том же порядке, в котором передан массив;
- id автоматически нормализуются (дубликаты/невалидные значения удаляются);
- карусель обрезается до `carousel_limit`.

Пример: установить заставку и карусель
```bash
curl -X PATCH "http://localhost:8000/api/v1/settings/showcase-media" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "showcase_hero_image_asset_id": 12345,
    "showcase_carousel_image_asset_ids": [20001, 20002, 20003]
  }'
```

Пример: очистить заставку
```bash
curl -X PATCH "http://localhost:8000/api/v1/settings/showcase-media" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "showcase_hero_image_asset_id": null
  }'
```

Пример: изменить только порядок карусели
```bash
curl -X PATCH "http://localhost:8000/api/v1/settings/showcase-media" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "showcase_carousel_image_asset_ids": [20003, 20001, 20002]
  }'
```
