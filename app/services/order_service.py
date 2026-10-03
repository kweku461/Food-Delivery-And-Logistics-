"""Checkout: turn a customer's cart into an order inside ONE database transaction.

Cart -> validate items -> calculate total -> create Order -> create OrderItems
     -> clear cart -> create Payment record (+ Delivery shell and status history)

If any step raises, the whole unit of work is rolled back: no half-created orders,
and the cart is left untouched so the customer can retry.
"""
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.enums import OrderStatus, PaymentStatus
from app.errors import BadRequestError, ConflictError, NotFoundError
from app.models import (
    Address, Delivery, DeliveryStatusHistory, Order, OrderItem, OrderStatusHistory, Payment,
    RestaurantBranch, User,
)
from app.schemas.order import OrderCreate
from app.enums import DeliveryStatus

TWO_PLACES = Decimal("0.01")


def money(v) -> Decimal:
    return Decimal(v).quantize(TWO_PLACES)


def item_unit_price(item) -> Decimal:
    return money(item.product.base_price + (item.variant.price_modifier if item.variant else 0))


def validate_cart_items(cart) -> list[dict]:
    """Every problem found is reported at once so the client can fix them in one go."""
    problems, lines = [], []
    for item in cart.items:
        p, v = item.product, item.variant
        label = p.name + (f" ({v.name})" if v else "")
        if not p.is_available:
            problems.append(f"'{label}' is currently unavailable")
        if p.restaurant_id != cart.restaurant_id:
            problems.append(f"'{label}' is not from the cart's restaurant")
        if item.variant_id is not None and (v is None or not v.is_available):
            problems.append(f"Variant for '{p.name}' is unavailable")
        if not p.restaurant.is_active:
            problems.append(f"Restaurant '{p.restaurant.name}' is not accepting orders")
        unit = item_unit_price(item)
        if unit < 0:
            problems.append(f"'{label}' has an invalid price")
        lines.append({"item": item, "unit_price": unit, "line_total": money(unit * item.quantity)})
    if problems:
        raise ConflictError("Some cart items cannot be ordered", sorted(set(problems)))
    return lines


def create_payment_record(db: Session, order: Order, method) -> Payment:
    payment = Payment(order_id=order.id, method=method, amount=order.total,
                      status=PaymentStatus.PENDING)
    db.add(payment)
    return payment


def create_order_from_cart(db: Session, user: User, data: OrderCreate) -> Order:
    try:
        cart = user.cart
        if cart is None or not cart.items:
            raise BadRequestError("Your cart is empty")

        address = db.get(Address, data.delivery_address_id)
        if address is None or address.user_id != user.id:
            raise NotFoundError("Delivery address not found")

        branch = db.get(RestaurantBranch, data.branch_id)
        if branch is None:
            raise NotFoundError("Restaurant branch not found")
        if branch.restaurant_id != cart.restaurant_id:
            raise BadRequestError("This branch does not belong to the restaurant in your cart")
        if not branch.is_active or not branch.restaurant.is_active:
            raise ConflictError("This branch is not accepting orders")

        lines = validate_cart_items(cart)
        subtotal = money(sum((l["line_total"] for l in lines), Decimal("0")))
        fee = money(branch.delivery_fee or 0)
        total = money(subtotal + fee)

        address_text = f"{address.address_line}, {address.city}" + (
            f" ({address.landmark})" if address.landmark else ""
        )
        order = Order(
            order_number="ORD-" + uuid.uuid4().hex[:10].upper(),
            customer_id=user.id, restaurant_id=branch.restaurant_id, branch_id=branch.id,
            status=OrderStatus.PENDING, subtotal=subtotal, delivery_fee=fee, total=total,
            notes=data.notes, delivery_address_id=address.id, delivery_address_text=address_text,
        )
        db.add(order)
        db.flush()

        for l in lines:
            it = l["item"]
            db.add(OrderItem(
                order_id=order.id, product_id=it.product_id, variant_id=it.variant_id,
                product_name=it.product.name, variant_name=it.variant.name if it.variant else None,
                unit_price=l["unit_price"], quantity=it.quantity, line_total=l["line_total"],
                notes=it.notes,
            ))
        db.add(OrderStatusHistory(order_id=order.id, from_status=None,
                                  to_status=OrderStatus.PENDING, changed_by_id=user.id,
                                  note="Order placed"))

        for it in list(cart.items):          # clear the cart
            db.delete(it)
        cart.restaurant_id = None

        create_payment_record(db, order, data.payment_method)

        delivery = Delivery(
            order_id=order.id, status=DeliveryStatus.UNASSIGNED,
            pickup_address=f"{branch.address_line}, {branch.city}", dropoff_address=address_text,
        )
        db.add(delivery)
        db.flush()
        db.add(DeliveryStatusHistory(delivery_id=delivery.id, from_status=None,
                                     to_status=DeliveryStatus.UNASSIGNED, changed_by_id=user.id,
                                     note="Delivery created"))
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(order)
    return order
