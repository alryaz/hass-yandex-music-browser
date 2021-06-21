import logging
from typing import Optional, TYPE_CHECKING, Union

from homeassistant.components.media_player import MediaPlayerEntity, SUPPORT_BROWSE_MEDIA
from homeassistant.components.media_player.const import (
    MEDIA_TYPE_ALBUM,
    MEDIA_TYPE_PLAYLIST,
    MEDIA_TYPE_TRACK,
)
from homeassistant.core import callback
from homeassistant.helpers.typing import HomeAssistantType

from custom_components.yandex_music_browser.const import DATA_BROWSER
from custom_components.yandex_music_browser.default import (
    async_get_music_browser,
    async_get_music_token,
)
from custom_components.yandex_music_browser.patches._base import _patch_root_async_browse_media
from custom_components.yandex_music_browser.media_browser import (
    YandexMusicBrowser,
    YandexMusicBrowserAuthenticationError,
    MAP_MEDIA_TYPE_TO_BROWSE,
    YandexBrowseMedia,
)

if TYPE_CHECKING:
    from custom_components.yandex_station.media_player import YandexStation

_LOGGER = logging.getLogger(__name__)


@callback
def _get_yandex_entities():
    from custom_components.yandex_station.media_player import YandexStation
    import gc

    return [
        obj
        for obj in gc.get_objects()
        if isinstance(obj, YandexStation) and obj.hass is not None and obj.enabled and obj._added
    ]


def _patch_yandex_station_get_attribute(self, attr: str):
    if attr == "supported_features":
        supported_features = object.__getattribute__(self, attr)
        supported_features |= SUPPORT_BROWSE_MEDIA

        return supported_features

    elif attr == "async_play_media":
        return _patch_yandex_station_async_play_media.__get__(self, self.__class__)

    elif attr == "async_browse_media":
        return _patch_yandex_station_async_browse_media.__get__(self, self.__class__)

    return object.__getattribute__(self, attr)


def _update_browse_object_for_cloud(
    music_browser: "YandexMusicBrowser",
    browse_object: YandexBrowseMedia,
    for_cloud: bool = True,
) -> YandexBrowseMedia:
    browse_object.media_content_id = browse_object.yandex_media_content_id
    browse_object.media_content_type = browse_object.yandex_media_content_type

    if for_cloud:
        if not browse_object.can_play:
            return browse_object

        if browse_object.media_content_type == MEDIA_TYPE_PLAYLIST:
            # We can't play playlists that are not ours
            if (
                ":" in browse_object.media_content_id
                and not browse_object.media_content_type.startswith(music_browser.user_id + ":")
            ):
                browse_object.can_play = False
    elif browse_object.media_content_type == MEDIA_TYPE_PLAYLIST:
        browse_object.can_play = True

    if browse_object.children:
        for child in browse_object.children:
            # noinspection PyTypeChecker
            _update_browse_object_for_cloud(music_browser, child, for_cloud=for_cloud)

    return browse_object


async def _patch_yandex_station_async_play_media(
    self: "YandexStation", media_type: str, media_id: str, **kwargs
):
    if media_type in MAP_MEDIA_TYPE_TO_BROWSE:
        if self.local_state:
            if media_type in ("track", "playlist", "album", "artist"):
                payload = {
                    "command": "playMusic",
                    "type": media_type,
                    "id": media_id,
                }
                return await self.glagol.send(payload)

            _LOGGER.warning("Unsupported glagol type")
            return

        elif media_type == MEDIA_TYPE_ALBUM:
            command = "альбом " + media_id

        elif media_type == MEDIA_TYPE_TRACK:
            command = "трек " + media_id

        elif media_type == MEDIA_TYPE_PLAYLIST:
            music_browser = self.hass.data.get(DATA_BROWSER)

            if ":" not in media_id:
                playlist_id = media_id
            elif media_id.startswith(music_browser.user_id):
                playlist_id = media_id.split(":")[-1]
            else:
                _LOGGER.warning(f"Unsupported playlist ID: {media_id}")
                return

            playlist_obj = await self.hass.async_add_executor_job(
                music_browser.client.users_playlists, playlist_id
            )

            if playlist_obj is None:
                _LOGGER.warning(f"Playlist not found: {media_id}")
                return

            command = "плейлист " + playlist_obj.title

        else:
            _LOGGER.warning(f"Unsupported cloud media type: {media_type}")
            return

        return await self.quasar.send(self.device, command)

    # noinspection PyUnresolvedReferences
    return await self.orig_async_play_media(media_type=media_type, media_id=media_id, **kwargs)


async def _patch_yandex_station_async_browse_media(
    self: "YandexStation",
    media_content_type: Optional[str] = None,
    media_content_id: Optional[str] = None,
) -> YandexBrowseMedia:
    music_browser = await async_get_music_browser(self)
    response = await _patch_root_async_browse_media(self, media_content_type, media_content_id)
    return _update_browse_object_for_cloud(
        music_browser,
        response,
        for_cloud=not self.local_state,
    )


async def _async_authenticate_using_yandex_station(entity: "YandexStation") -> str:
    # Authenticate using Yandex Station entity
    session = entity.quasar.session
    music_token = session.music_token
    if music_token is None:
        x_token = session.x_token
        if x_token is None:
            raise ValueError("x_token is empty!")
        music_token = await session.get_music_token(x_token)
        session.music_token = music_token

    return music_token


#################################################################################
# Exported procedures
#################################################################################


def install(hass: HomeAssistantType):
    try:
        from custom_components.yandex_station.media_player import YandexStation
    except ImportError:
        _LOGGER.warning("Installation for Yandex Station halted")
    else:
        if YandexStation.__getattribute__ is not _patch_yandex_station_get_attribute:
            _LOGGER.debug("Patching __getattribute__ for Yandex Station")
            YandexStation.orig__getattribute__ = YandexStation.__getattribute__
            YandexStation.__getattribute__ = _patch_yandex_station_get_attribute

        _get_yandex_entities()


def uninstall(hass: HomeAssistantType):
    try:
        from custom_components.yandex_station.media_player import YandexStation
    except ImportError:
        pass
    else:
        if YandexStation.__getattribute__ is _patch_yandex_station_get_attribute:
            # noinspection PyUnresolvedReferences
            YandexStation.__getattribute__ = YandexStation.orig__getattribute__


async def async_authenticate(on: Union[HomeAssistantType, "MediaPlayerEntity"]):
    try:
        from custom_components.yandex_station.media_player import YandexStation
    except ImportError:
        raise YandexMusicBrowserAuthenticationError(
            "Could not authenticate: Component is not installed"
        )

    else:
        authentication = None

        # Stage 1: Authenticate using caller entity
        if isinstance(on, YandexStation):
            hass = on.hass
            if hass is None:
                _LOGGER.error(
                    "Home Assistant object is not yet available, and therefore cannot be extrapolated"
                )

            try:
                # Authenticate using Yandex Station entity
                return await _async_authenticate_using_yandex_station(on)
            except BaseException as e:
                _LOGGER.error("Could not authenticate using Yandex Station entity: %s", e)
        else:
            hass = on

        # Stage 2: Authenticate using other entities
        if authentication is None:
            yandex_entities = _get_yandex_entities()

            for entity in yandex_entities:
                if entity is on:
                    continue
                if hass is None and entity.hass:
                    hass = entity.hass
                try:
                    # Authenticate using auxilliary Yandex entity
                    return await _async_authenticate_using_yandex_station(entity)
                except BaseException as e:
                    _LOGGER.error("Could not authenticate using Yandex Station entity: %s", e)

        # Stage 3: Authenticate using stored configuration
        if authentication is None and hass is not None:
            from custom_components.yandex_station import DATA_CONFIG, DOMAIN

            try:
                yandex_station_config = hass.data[DOMAIN][DATA_CONFIG]
            except KeyError:
                raise YandexMusicBrowserAuthenticationError(
                    "Yandex Station configuration not found"
                )

            music_token = yandex_station_config.get("music_token")
            if music_token is not None:
                return music_token

            x_token = yandex_station_config.get("x_token")

            try:
                return await async_get_music_token(x_token)
            except BaseException as e:
                _LOGGER.error("Could not authenticate using Yandex Station config: %s", e)

        # Fail miserably
        raise YandexMusicBrowserAuthenticationError("Could not authenticate using Yandex Station")
