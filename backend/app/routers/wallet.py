"""
/api/wallet/*  — ví tiền: nạp tiền (chuyển khoản thủ công, admin duyệt), mua gói, lịch sử.

Luồng nạp tiền: user tạo đơn (POST /topup) -> server sinh mã đơn ngẫu nhiên không trùng + nội dung
chuyển khoản gợi ý -> user chuyển khoản ngoài đời rồi bấm "đã chuyển tiền" (POST /topup/{id}/ack,
chỉ để đánh dấu cho admin biết, KHÔNG tự cộng tiền) -> admin vào /api/admin/orders duyệt tay
(routers/admin.py) mới thực sự cộng ví. Việc cộng tiền không bao giờ do user tự kích hoạt được.
"""
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import err, require_user
from app.models.db import User, WalletOrder, WalletTransaction, get_session
from app.services.quota import plan_active, remaining_quota

router = APIRouter(prefix="/api/wallet", tags=["wallet"])


def _new_order_code() -> str:
    # "nangcapgoi" + chuỗi random không dấu, khó trùng giữa nhiều đơn cùng lúc
    return "nangcapgoi" + secrets.token_hex(5)


async def _wallet_payload(user: User) -> dict:
    used, limit = remaining_quota(user)
    return {
        "wallet_balance": user.wallet_balance,
        "currency": settings.WALLET_CURRENCY,
        "plan": user.plan if plan_active(user) else "free",
        "plan_expires_at": user.plan_expires_at.isoformat() if user.plan_expires_at else None,
        "video_quota_used": used, "video_quota_limit": limit,
        "plan_prices": settings.PLAN_PRICES,
        "plan_daily_limits": settings.PLAN_DAILY_LIMIT,
    }


@router.get("")
async def get_wallet(user: User = Depends(require_user)):
    return await _wallet_payload(user)


# ---------------------------------------------------------------------------
# Nạp tiền
# ---------------------------------------------------------------------------

class TopupIn(BaseModel):
    amount: int = Field(gt=0)


@router.post("/topup")
async def create_topup(body: TopupIn, user: User = Depends(require_user), session: AsyncSession = Depends(get_session)):
    if body.amount < settings.WALLET_TOPUP_MIN or body.amount > settings.WALLET_TOPUP_MAX:
        raise err(400, "BAD_AMOUNT",
                  f"Số tiền nạp phải từ {settings.WALLET_TOPUP_MIN:,} đến {settings.WALLET_TOPUP_MAX:,} {settings.WALLET_CURRENCY}.")
    code = _new_order_code()
    # cực hiếm khi trùng (5 byte random hex), nhưng vẫn kiểm tra cho chắc vì order_code là unique
    for _ in range(5):
        if not (await session.execute(select(WalletOrder.id).where(WalletOrder.order_code == code))).scalar_one_or_none():
            break
        code = _new_order_code()
    order = WalletOrder(order_code=code, user_id=user.id, amount=body.amount)
    session.add(order)
    await session.commit()
    return {
        "order_id": order.id,
        "order_code": order.order_code,
        "amount": order.amount,
        "status": order.status,
        "receiver_name": settings.WALLET_RECEIVER_NAME,
        "receiver_account": settings.WALLET_RECEIVER_ACCOUNT,
        "transfer_note": order.order_code,
        "instructions": f"Nạp bằng gachthefast, chuyển vào {settings.WALLET_RECEIVER_ACCOUNT}, "
                        f"nội dung chuyển khoản ghi đúng: {order.order_code}",
    }


@router.post("/topup/{order_id}/ack")
async def ack_topup(order_id: str, user: User = Depends(require_user), session: AsyncSession = Depends(get_session)):
    """User bấm 'Xác nhận đã chuyển tiền' — chỉ đánh dấu cho admin biết, KHÔNG cộng ví."""
    order = await session.get(WalletOrder, order_id)
    if not order or order.user_id != user.id:
        raise err(404, "ORDER_NOT_FOUND", "Không tìm thấy đơn nạp tiền.")
    if order.status != "pending":
        raise err(400, "ORDER_NOT_PENDING", "Đơn này đã được xử lý, không cần xác nhận lại.")
    order.user_confirmed_at = datetime.utcnow()
    await session.commit()
    return {"ok": True, "status": order.status}


@router.get("/topup/history")
async def topup_history(user: User = Depends(require_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(WalletOrder).where(WalletOrder.user_id == user.id).order_by(WalletOrder.created_at.desc()).limit(100)
    )).scalars().all()
    return [{
        "order_id": o.id, "order_code": o.order_code, "amount": o.amount, "status": o.status,
        "reject_reason": o.reject_reason, "created_at": o.created_at.isoformat(),
        "user_confirmed_at": o.user_confirmed_at.isoformat() if o.user_confirmed_at else None,
    } for o in rows]


@router.get("/transactions")
async def transaction_history(user: User = Depends(require_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(WalletTransaction).where(WalletTransaction.user_id == user.id)
        .order_by(WalletTransaction.created_at.desc()).limit(200)
    )).scalars().all()
    return [{
        "id": t.id, "kind": t.kind, "amount": t.amount, "balance_after": t.balance_after,
        "note": t.note, "created_at": t.created_at.isoformat(),
    } for t in rows]


# ---------------------------------------------------------------------------
# Mua gói
# ---------------------------------------------------------------------------

class PurchasePlanIn(BaseModel):
    plan: str
    months: int = Field(ge=1, le=24)


@router.post("/plan/purchase")
async def purchase_plan(body: PurchasePlanIn, user: User = Depends(require_user), session: AsyncSession = Depends(get_session)):
    plan = body.plan.strip().lower()
    if plan not in settings.PLAN_PRICES:
        raise err(400, "BAD_PLAN", f"Gói không hợp lệ. Chọn 1 trong: {', '.join(settings.PLAN_PRICES)}.")
    cost = settings.PLAN_PRICES[plan] * body.months

    # Lưu ý: app chạy 1 worker (RAM job queue, xem main.py) nên rủi ro 2 request mua đồng thời
    # trừ ví 2 lần là rất thấp; không dùng SELECT FOR UPDATE vì SQLite (mặc định) không hỗ trợ.
    db_user = await session.get(User, user.id)
    if db_user.wallet_balance < cost:
        raise err(400, "INSUFFICIENT_BALANCE",
                  f"Số dư không đủ. Cần {cost:,} {settings.WALLET_CURRENCY}, ví đang có {db_user.wallet_balance:,}.")

    now = datetime.utcnow()
    # gia hạn nếu đang dùng đúng gói này và còn hạn, ngược lại tính từ bây giờ (đổi gói = reset hạn)
    base = db_user.plan_expires_at if (db_user.plan == plan and db_user.plan_expires_at and db_user.plan_expires_at > now) else now
    db_user.plan = plan
    db_user.plan_expires_at = base + timedelta(days=30 * body.months)
    db_user.wallet_balance -= cost
    session.add(WalletTransaction(
        user_id=db_user.id, kind="plan_purchase", amount=-cost, balance_after=db_user.wallet_balance,
        note=f"Mua gói {plan.upper()} x{body.months} tháng",
    ))
    await session.commit()
    return {"ok": True, "plan": db_user.plan, "plan_expires_at": db_user.plan_expires_at.isoformat(),
            "wallet_balance": db_user.wallet_balance}
