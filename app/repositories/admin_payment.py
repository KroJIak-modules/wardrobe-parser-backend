from sqlalchemy import func, select, text

from app.models.admin_payment import AdminPayment


class PaymentRepository:
    def __init__(self, db):
        self.db = db

    def next_order(self):
        value = self.db.execute(text("SELECT nextval('admin_payment_order_seq')")).scalar_one()
        return f"AS-{value}"

    def get(self, payment_id, lock=False):
        query = select(AdminPayment).where(AdminPayment.id == payment_id)
        if lock:
            query = query.with_for_update()
        return self.db.execute(query).scalar_one_or_none()

    def by_order(self, order, lock=False):
        query = select(AdminPayment).where(AdminPayment.order_number == order)
        if lock:
            query = query.with_for_update()
        return self.db.execute(query).scalar_one_or_none()

    def list(self, limit, offset):
        total = self.db.scalar(select(func.count()).select_from(AdminPayment))
        items = self.db.execute(select(AdminPayment).order_by(AdminPayment.created_at.desc(), AdminPayment.id.desc()).limit(limit).offset(offset)).scalars().all()
        return items, total
