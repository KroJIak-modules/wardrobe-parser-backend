from sqlalchemy.orm import Session

from app.models.product_image import ProductImage


class ProductImageRepository:
    @staticmethod
    def replace_images(db: Session, product_id: int, urls: list[str]) -> None:
        db.query(ProductImage).filter(ProductImage.product_id == product_id).delete()
        for index, url in enumerate(urls):
            db.add(ProductImage(product_id=product_id, url=url, sort_order=index))
        db.flush()
