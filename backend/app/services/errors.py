class JobCancelled(Exception):
    """Người dùng đã huỷ job giữa chừng."""


import asyncio


async def gather_or_cancel(*coros):
    """Như asyncio.gather nhưng nếu 1 tác vụ lỗi/bị huỷ thì HUỶ hết các tác vụ còn lại (không để chạy mồ côi)."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
