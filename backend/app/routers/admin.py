"""
/api/admin/*  — chỉ tài khoản is_admin=True truy cập được (require_admin, xem core/deps.py).

Duyệt đơn: cộng tiền thật vào ví (chỉ nơi DUY NHẤT trong hệ thống được cộng ví do nạp tiền).
Từ chối: giữ nguyên, không cộng cũng không trừ, có lý do.
Hoàn tiền: chỉ áp dụng cho đơn đã confirmed (đã cộng tiền) — trừ lại đúng số tiền đã cộng.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import err, require_admin
from app.models.db import User, WalletOrder, WalletTransaction, get_session

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _order_payload(o: WalletOrder, requester_email: str | None = None) -> dict:
    return {
        "order_id": o.id, "order_code": o.order_code, "user_id": o.user_id,
        "amount": o.amount, "status": o.status, "reject_reason": o.reject_reason,
        "created_at": o.created_at.isoformat(),
        "user_confirmed_at": o.user_confirmed_at.isoformat() if o.user_confirmed_at else None,
        "decided_at": o.decided_at.isoformat() if o.decided_at else None,
    }


@router.get("/orders/pending")
async def list_pending_orders(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(WalletOrder, User.email).join(User, User.id == WalletOrder.user_id)
        .where(WalletOrder.status == "pending").order_by(WalletOrder.created_at.asc())
    )).all()
    return [{**_order_payload(o), "user_email": email} for o, email in rows]


@router.get("/orders/lookup")
async def lookup_order(code: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    row = (await session.execute(
        select(WalletOrder, User.email).join(User, User.id == WalletOrder.user_id)
        .where(WalletOrder.order_code == code.strip())
    )).first()
    if not row:
        raise err(404, "ORDER_NOT_FOUND", "Không tìm thấy đơn với mã này.")
    o, email = row
    return {**_order_payload(o), "user_email": email}


@router.post("/orders/{order_id}/approve")
async def approve_order(order_id: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    order = await session.get(WalletOrder, order_id)
    if not order:
        raise err(404, "ORDER_NOT_FOUND", "Không tìm thấy đơn nạp tiền.")
    if order.status != "pending":
        raise err(400, "ORDER_NOT_PENDING", "Đơn này đã được xử lý trước đó.")
    user = await session.get(User, order.user_id)
    user.wallet_balance += order.amount
    order.status = "confirmed"
    order.decided_at = datetime.utcnow()
    order.decided_by = admin.id
    session.add(WalletTransaction(
        user_id=user.id, kind="topup", amount=order.amount, balance_after=user.wallet_balance,
        note=f"Nạp tiền đơn {order.order_code} được duyệt", ref_order_id=order.id,
    ))
    await session.commit()
    return {"ok": True, "status": order.status, "user_wallet_balance": user.wallet_balance}


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/orders/{order_id}/reject")
async def reject_order(order_id: str, body: RejectIn, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    order = await session.get(WalletOrder, order_id)
    if not order:
        raise err(404, "ORDER_NOT_FOUND", "Không tìm thấy đơn nạp tiền.")
    if order.status != "pending":
        raise err(400, "ORDER_NOT_PENDING", "Đơn này đã được xử lý trước đó.")
    order.status = "rejected"
    order.reject_reason = body.reason
    order.decided_at = datetime.utcnow()
    order.decided_by = admin.id
    await session.commit()
    return {"ok": True, "status": order.status, "reject_reason": order.reject_reason}


@router.post("/orders/{order_id}/refund")
async def refund_order(order_id: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    order = await session.get(WalletOrder, order_id)
    if not order:
        raise err(404, "ORDER_NOT_FOUND", "Không tìm thấy đơn nạp tiền.")
    if order.status != "confirmed":
        raise err(400, "ORDER_NOT_REFUNDABLE", "Chỉ hoàn được đơn đã duyệt (confirmed).")
    user = await session.get(User, order.user_id)
    user.wallet_balance -= order.amount
    order.status = "refunded"
    order.decided_at = datetime.utcnow()
    order.decided_by = admin.id
    session.add(WalletTransaction(
        user_id=user.id, kind="refund", amount=-order.amount, balance_after=user.wallet_balance,
        note=f"Hoàn tiền đơn {order.order_code}", ref_order_id=order.id,
    ))
    await session.commit()
    return {"ok": True, "status": order.status, "user_wallet_balance": user.wallet_balance}
