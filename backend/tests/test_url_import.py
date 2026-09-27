import pytest

from app.services.url_import_service import fetch_metadata, UnsupportedURLError


@pytest.mark.asyncio
async def test_fetch_metadata_rejects_disallowed_domain():
    with pytest.raises(UnsupportedURLError):
        await fetch_metadata("https://random-site-not-allowlisted.example/video123")


@pytest.mark.asyncio
async def test_fetch_metadata_rejects_non_http_scheme():
    with pytest.raises(UnsupportedURLError):
        await fetch_metadata("javascript:alert(1)")
