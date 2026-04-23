"""
Provider Registry
=================
Central registry that maps Platform enum values to provider instances.
Use get_provider() anywhere in the codebase to get the right adapter.
"""

from app.models.models import Platform
from app.providers.base import PlatformProvider
from app.providers.instagram import InstagramProvider
from app.providers.tiktok import TikTokProvider
from app.providers.youtube import YouTubeProvider

_registry: dict[Platform, PlatformProvider] = {
    Platform.TIKTOK: TikTokProvider(),
    Platform.INSTAGRAM: InstagramProvider(),
    Platform.YOUTUBE: YouTubeProvider(),
}


def get_provider(platform: Platform) -> PlatformProvider:
    """Return the provider instance for a given platform."""
    provider = _registry.get(platform)
    if not provider:
        raise ValueError(f"No provider registered for platform: {platform}")
    return provider


def all_providers() -> dict[Platform, PlatformProvider]:
    return dict(_registry)
