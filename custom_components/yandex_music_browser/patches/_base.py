import logging
from typing import Optional, Union

from homeassistant.components.media_player import BrowseError
from homeassistant.helpers.typing import HomeAssistantType

from custom_components.yandex_music_browser import ROOT_MEDIA_CONTENT_TYPE, YandexBrowseMedia
from custom_components.yandex_music_browser.default import async_get_music_browser

_LOGGER = logging.getLogger(__name__)


async def _patch_root_async_browse_media(
    self: Union["MediaPlayerEntity", HomeAssistantType],
    media_content_type: Optional[str] = None,
    media_content_id: Optional[str] = None,
    fetch_children: bool = True,
) -> YandexBrowseMedia:
    music_browser = await async_get_music_browser(self)

    if media_content_type is None:
        media_content_type = ROOT_MEDIA_CONTENT_TYPE

    _LOGGER.debug("Requesting browse: %s / %s" % (media_content_type, media_content_id))
    response = await (
        self if isinstance(self, HomeAssistantType) else self.hass
    ).async_add_executor_job(
        music_browser.generate_browse_from_media,
        (media_content_type, media_content_id),
        fetch_children,  # fetch_children
        True,  # cache_garbage_collection
    )

    if response is None:
        _LOGGER.debug("Media type: %s", type(media_content_type))
        raise BrowseError(f"Media not found: {media_content_type} / {media_content_id}")

    return response
