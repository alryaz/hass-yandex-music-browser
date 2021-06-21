__all__ = [
    "config_flow",
    "const",
    "media_browser",
]

import asyncio
import datetime
import logging
from datetime import timedelta
from typing import Final, Mapping, Optional, TYPE_CHECKING

import aiohttp
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.media_player import (
    BrowseError,
    DOMAIN,
    MediaPlayerEntity,
    SUPPORT_BROWSE_MEDIA,
)
from homeassistant.components.media_player.const import (
    MEDIA_TYPE_ALBUM,
    MEDIA_TYPE_PLAYLIST,
    MEDIA_TYPE_TRACK,
    SUPPORT_PLAY_MEDIA,
)
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.const import *
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType, HomeAssistantType
from homeassistant.loader import bind_hass

from custom_components.yandex_music_browser.const import (
    CONF_CACHE_TTL,
    CONF_CLASS,
    CONF_HEIGHT,
    CONF_IMAGE,
    CONF_ITEMS,
    CONF_LANGUAGE,
    CONF_LYRICS,
    CONF_MENU_OPTIONS,
    CONF_SHOW_HIDDEN,
    CONF_THUMBNAIL_RESOLUTION,
    CONF_TITLE,
    CONF_WIDTH,
    DATA_BROWSER,
    DATA_YAML_CONFIG,
    DOMAIN,
    ROOT_MEDIA_CONTENT_TYPE,
    SUPPORTED_BROWSER_LANGUAGES,
)
from custom_components.yandex_music_browser.media_browser import (
    BrowseTree,
    DEFAULT_MENU_OPTIONS,
    DEFAULT_THUMBNAIL_RESOLUTION,
    MAP_MEDIA_TYPE_TO_BROWSE,
    PLAY_URL_BY_TYPES,
    YandexBrowseMedia,
    YandexMusicBrowser,
    YandexMusicBrowserAuthenticationError,
    YandexMusicBrowserException,
    sanitize_media_link,
)

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from custom_components.yandex_station.media_player import YandexStation


def process_width_height_dict(resolution: dict):
    if CONF_WIDTH in resolution:
        if CONF_HEIGHT not in resolution:
            resolution[CONF_HEIGHT] = resolution[CONF_WIDTH]
    elif CONF_HEIGHT in resolution:
        resolution[CONF_WIDTH] = resolution[CONF_HEIGHT]
    else:
        raise vol.Invalid(f"at least one parameter ({CONF_WIDTH}, {CONF_HEIGHT}) must be provided")
    return f"{resolution[CONF_WIDTH]}x{resolution[CONF_HEIGHT]}"


def process_width_height_str(resolution: str):
    parts = resolution.split("x")

    try:
        width = int(parts[0])
        if len(parts) == 1:
            height = width
        elif len(parts) == 2:
            height = int(parts[1])
        else:
            raise vol.Invalid("one or two dimensional parameters are required")

        if width < 50 or height < 50:
            raise vol.Invalid("min dimension is 50px")
        if width > 1000 or height > 1000:
            raise vol.Invalid("max dimension is 1000px")

    except ValueError:
        raise vol.Invalid(
            f"dimensions must be presented in a <{CONF_WIDTH}>x<{CONF_HEIGHT}> format"
        )

    return {CONF_WIDTH: width, CONF_HEIGHT: height}


def validate_parsed_menu_options(menu_options: Mapping):
    from custom_components.yandex_music_browser.media_browser import BrowseTree

    try:
        BrowseTree.from_map(menu_options, validate=True)
    except (ValueError, IndexError, TypeError) as e:
        raise vol.Invalid("invalid menu options: %s" % str(e))

    return menu_options


def wrap_sanitize_media_link(x):
    try:
        return sanitize_media_link(x)
    except BaseException as e:
        raise vol.Invalid("media type error: %s" % e)


MENU_OPTIONS_VALIDATOR = vol.All(
    lambda x: {CONF_ITEMS: x} if isinstance(x, list) else x,
    vol.Schema(
        {
            vol.Optional(CONF_TITLE, default=None): vol.Any(vol.Equal(None), cv.string),
            vol.Optional(CONF_IMAGE, default=None): vol.Any(vol.Equal(None), cv.string),
            vol.Optional(CONF_CLASS, default=None): vol.Any(vol.Equal(None), cv.string),
            vol.Optional(CONF_ITEMS, default=[]): [
                vol.Any(
                    wrap_sanitize_media_link,
                    lambda x: MENU_OPTIONS_VALIDATOR(x),
                )
            ],
        }
    ),
)


THUMBNAIL_RESOLUTION_VALIDATOR = vol.All(
    vol.Any(
        vol.All(cv.string, process_width_height_str),
        process_width_height_dict,
    ),
    vol.Schema(
        {
            vol.Optional(CONF_WIDTH): cv.positive_int,
            vol.Optional(CONF_HEIGHT): cv.positive_int,
        }
    ),
)
LOCAL_TIMEZONE = datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo
IS_IN_RUSSIA = timedelta(hours=2) <= LOCAL_TIMEZONE.utcoffset(None) <= timedelta(hours=12)

CONF_DEBUG: Final = "debug"

DEFAULT_LANGUAGE: Final = "ru" if IS_IN_RUSSIA else "en"

CONF_CREDENTIALS = "credentials"
CONF_X_TOKEN = "x_token"

CONFIG_ENTRY_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CACHE_TTL, default=600): cv.positive_float,
        vol.Optional(CONF_TIMEOUT, default=15): cv.positive_float,
        vol.Optional(CONF_LANGUAGE, default=DEFAULT_LANGUAGE): vol.All(
            vol.Lower, vol.In(SUPPORTED_BROWSER_LANGUAGES)
        ),
        vol.Optional(CONF_SHOW_HIDDEN, default=False): cv.boolean,
        vol.Optional(CONF_LYRICS, default=False): cv.boolean,
        vol.Optional(CONF_MENU_OPTIONS, default=lambda: DEFAULT_MENU_OPTIONS.to_map()): vol.All(
            MENU_OPTIONS_VALIDATOR, validate_parsed_menu_options
        ),
        vol.Optional(CONF_THUMBNAIL_RESOLUTION): THUMBNAIL_RESOLUTION_VALIDATOR,
        vol.Optional(CONF_DEBUG, default=False): cv.boolean,
        vol.Optional(CONF_CREDENTIALS, default=lambda: []): vol.All(
            cv.ensure_list,
            [
                vol.Any(
                    vol.Schema(
                        {
                            vol.Required(CONF_USERNAME): cv.string,
                            vol.Required(CONF_PASSWORD): cv.string,
                        }
                    ),
                    vol.Schema(
                        {
                            vol.Required(CONF_X_TOKEN): cv.string,
                        }
                    ),
                )
            ],
        ),
    }
)

CONFIG_SCHEMA: Final = vol.Schema(
    {
        vol.Optional(DOMAIN): CONFIG_ENTRY_SCHEMA,
    },
    extra=vol.ALLOW_EXTRA,
)


#################################################################################
# Authentication procedures
#################################################################################


async def async_get_music_token(x_token: str):
    """Get music token using x-token. Adapted from AlexxIT/YandexStation."""
    _LOGGER.debug("Get music token")

    payload = {
        # Thanks to https://github.com/MarshalX/yandex-music-api/
        "client_secret": "53bc75238f0c4d08a118e51fe9203300",
        "client_id": "23cabbbdc6cd418abb4b39c32c41195d",
        "grant_type": "x-token",
        "access_token": x_token,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post("https://oauth.mobile.yandex.net/1/token", data=payload) as r:
            resp = await r.json()

    assert "access_token" in resp, resp
    return resp["access_token"]


async def async_authenticate_using_yandex_station(entity: "YandexStation") -> str:
    session = entity.quasar.session
    music_token = session.music_token
    if music_token is None:
        x_token = session.x_token
        if x_token is None:
            raise ValueError("x_token is empty!")
        music_token = await session.get_music_token(x_token)
        session.music_token = music_token

    return music_token


async def async_authenticate_using_yandex_station_config(entity: "MediaPlayerEntity") -> str:
    hass = entity.hass

    from custom_components.yandex_station import DATA_CONFIG, DOMAIN

    try:
        yandex_station_config = hass.data[DOMAIN][DATA_CONFIG]
    except KeyError:
        raise YandexMusicBrowserAuthenticationError("Yandex Station configuration not found")

    music_token = yandex_station_config.get("music_token")
    if music_token is not None:
        return music_token

    x_token = yandex_station_config.get("x_token")
    return await async_get_music_token(x_token)


async def async_authenticate_using_config_credentials(entity: "MediaPlayerEntity") -> "Client":
    hass = entity.hass
    config = hass.data[DOMAIN]
    credentials = config.get(CONF_CREDENTIALS)
    if not credentials:
        raise YandexMusicBrowserAuthenticationError("No credentials provided")

    from yandex_music import Client

    for credential in credentials:
        if CONF_X_TOKEN in credential:
            x_token = credential[CONF_X_TOKEN]

            try:
                token = await async_get_music_token(x_token)
            except BaseException as e:
                _LOGGER.debug(f'Could not get music token from "...{x_token[-6:]}": {e}')
            else:
                return token

        else:
            username = credential[CONF_USERNAME]
            password = credential[CONF_PASSWORD]

            try:
                return await hass.async_add_executor_job(
                    Client.from_credentials, username, password
                )
            except BaseException as e:
                _LOGGER.debug(f'Could not get music token from "...{username[-6:]}": {e}')

    raise YandexMusicBrowserAuthenticationError("No credentials found to perform authentication")


#################################################################################
# Base patches
#################################################################################


async def async_get_music_browser(entity: MediaPlayerEntity) -> YandexMusicBrowser:
    hass = entity.hass
    music_browser = hass.data.get(DATA_BROWSER)

    if isinstance(music_browser, asyncio.Future):
        music_browser = await music_browser

    elif music_browser is None:
        future_obj = hass.loop.create_future()
        hass.data[DATA_BROWSER] = future_obj

        try:
            authentication = None
            try:
                from custom_components.yandex_station.media_player import YandexStation
            except ImportError:
                pass
            else:
                try:
                    _LOGGER.debug("Attempting Yandex Station authentication")
                    if isinstance(entity, YandexStation):
                        authentication = await async_authenticate_using_yandex_station(entity)
                    else:
                        authentication = await async_authenticate_using_yandex_station_config(
                            entity
                        )
                except BaseException as e:
                    _LOGGER.error("Could not perform Yandex Station authentication: %s", e)
                    pass

            if authentication is None:
                _LOGGER.debug("Attempting provided credentials authentication")
                try:
                    authentication = await async_authenticate_using_config_credentials(entity)
                except BaseException as e:
                    _LOGGER.error("Could not authenticate using any provided credentials: %s", e)

            if authentication is None:
                raise YandexMusicBrowserAuthenticationError(
                    "could not authenticate using any method"
                )

            music_browser = await hass.async_add_executor_job(
                YandexMusicBrowser,
                authentication,
                hass.data[DOMAIN],
            )

        except BaseException as e:
            hass.data[DATA_BROWSER] = None
            future_obj.set_exception(e)
            raise
        else:
            hass.data[DATA_BROWSER] = music_browser
            future_obj.set_result(music_browser)

    return music_browser


async def _patch_root_yandex_async_browse_media(
    self: "MediaPlayerEntity",
    media_content_type: Optional[str] = None,
    media_content_id: Optional[str] = None,
    fetch_children: bool = True,
) -> YandexBrowseMedia:
    music_browser = await async_get_music_browser(self)

    if media_content_type is None:
        media_content_type = ROOT_MEDIA_CONTENT_TYPE

    _LOGGER.debug("Requesting browse: %s / %s" % (media_content_type, media_content_id))
    response = await self.hass.async_add_executor_job(
        music_browser.generate_browse_from_media,
        (media_content_type, media_content_id),
        fetch_children,  # fetch_children
        True,  # cache_garbage_collection
    )

    if response is None:
        _LOGGER.debug("Media type: %s", type(media_content_type))
        raise BrowseError(f"Media not found: {media_content_type} / {media_content_id}")

    return response


#################################################################################
# Patches for Yandex Station component
#################################################################################


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
    browse_object.media_content_type = browse_object.media_content_type

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
    response = await _patch_root_yandex_async_browse_media(
        self, media_content_type, media_content_id
    )
    return _update_browse_object_for_cloud(
        music_browser,
        response,
        for_cloud=not self.local_state,
    )


@callback
def _async_update_yandex_entities():
    from custom_components.yandex_station.media_player import YandexStation
    import gc

    for obj in gc.get_objects():
        if isinstance(obj, YandexStation) and obj.hass is not None and obj.enabled and obj._added:
            obj.async_schedule_update_ha_state(force_refresh=True)


def _install_yandex_station():
    try:
        from custom_components.yandex_station.media_player import YandexStation
    except ImportError:
        _LOGGER.warning("Installation for Yandex Station halted")
    else:
        if YandexStation.__getattribute__ is not _patch_yandex_station_get_attribute:
            _LOGGER.debug("Patching __getattribute__ for Yandex Station")
            YandexStation.orig__getattribute__ = YandexStation.__getattribute__
            YandexStation.__getattribute__ = _patch_yandex_station_get_attribute

        _async_update_yandex_entities()


def _uninstall_yandex_station():
    try:
        from custom_components.yandex_station.media_player import YandexStation
    except ImportError:
        pass
    else:
        if YandexStation.__getattribute__ is _patch_yandex_station_get_attribute:
            # noinspection PyUnresolvedReferences
            YandexStation.__getattribute__ = YandexStation.orig__getattribute__


#################################################################################
# Patches for generic component
#################################################################################


def _patch_generic_get_attribute(self, attr: str):
    if attr == "supported_features":
        supported_features = object.__getattribute__(self, attr)
        if supported_features is not None and supported_features & SUPPORT_PLAY_MEDIA:
            return supported_features | SUPPORT_BROWSE_MEDIA
        return supported_features

    elif attr == "async_play_media":
        return _patch_generic_async_play_media.__get__(self, self.__class__)

    elif attr == "async_browse_media":
        return _patch_generic_async_browse_media.__get__(self, self.__class__)

    return object.__getattribute__(self, attr)


def _update_browse_object_for_url(
    music_browser: "YandexMusicBrowser",
    browse_object: YandexBrowseMedia,
) -> YandexBrowseMedia:
    browse_object.media_content_type = "yandex"
    browse_object.media_content_id = (
        browse_object.yandex_media_content_type + ":" + browse_object.yandex_media_content_id
    )

    if browse_object.children:
        browse_object.children = list(
            map(lambda x: _update_browse_object_for_url(music_browser, x), browse_object.children)
        )

    media_object = browse_object.media_object
    browse_object.can_play = media_object and media_object.__class__ in PLAY_URL_BY_TYPES

    return browse_object


async def _patch_generic_async_play_media(
    self: "MediaPlayerEntity",
    media_type: Optional[str] = None,
    media_id: Optional[str] = None,
    **kwargs,
):
    _LOGGER.debug("Generic async play media call: (%s) (%s) %s", media_type, media_id, kwargs)
    if media_type == "yandex":
        media_type, _, media_id = media_id.partition(":")

        _LOGGER.debug("Willing to play Yandex Media: %s - %s", media_type, media_id)
        browse_object = await _patch_root_yandex_async_browse_media(self, media_type, media_id)
        media_object = getattr(browse_object, "media_object", None)
        if media_object:
            media_object_type = type(media_object)
            if media_object_type in PLAY_URL_BY_TYPES:
                result = await self.hass.async_add_executor_job(
                    PLAY_URL_BY_TYPES[media_object_type], media_object
                )
                if result:
                    media_id, media_type = result
                    _LOGGER.debug("Retrieved URL: %s", media_id)
                    return await object.__getattribute__(self, "async_play_media")(
                        media_id=media_id,
                        media_type=media_type,
                        **kwargs,
                    )

        raise YandexMusicBrowserException(
            "could not play unsupported type: %s - %s" % (media_type, media_id)
        )

    return await object.__getattribute__(self, "async_play_media")(
        media_type=media_type, media_id=media_id, **kwargs
    )


async def _patch_generic_async_browse_media(
    self: "MediaPlayerEntity",
    media_content_type: Optional[str] = None,
    media_content_id: Optional[str] = None,
):
    _LOGGER.debug(
        "Generic async browse media call: (%s) (%s)", media_content_type, media_content_id
    )
    yandex_browse_object = None
    if media_content_type == "yandex":
        media_content_type, _, media_content_id = media_content_id.partition(":")
        yandex_browse_object = await _patch_root_yandex_async_browse_media(
            self, media_content_type, media_content_id
        )
        result_object = yandex_browse_object

    else:
        async_browse_media_local = self.__class__.async_browse_media
        result_object = None
        if async_browse_media_local is not _patch_generic_async_browse_media:
            try:
                result_object = await async_browse_media_local(
                    self, media_content_type, media_content_id
                )
            except NotImplementedError:
                pass

        if (
            media_content_type is None or media_content_type == ROOT_MEDIA_CONTENT_TYPE
        ) and not media_content_id:
            yandex_browse_object = await _patch_root_yandex_async_browse_media(
                self, media_content_type, media_content_id, fetch_children=not result_object
            )
            if result_object:
                current_children = [*(result_object.children or [])]
                current_children.append(yandex_browse_object)
                result_object.children = current_children
            else:
                result_object = yandex_browse_object

    if result_object is None:
        raise BrowseError("Could not find required object")

    if yandex_browse_object is not None:
        await self.hass.async_add_executor_job(
            _update_browse_object_for_url,
            await async_get_music_browser(self),
            yandex_browse_object,
        )

    _LOGGER.debug("Resulting object: %s", result_object)
    _LOGGER.debug("Resulting children: %s", result_object.children)

    return result_object


def _install_generic():
    from homeassistant.components.media_player import MediaPlayerEntity

    if MediaPlayerEntity.__getattribute__ is not _patch_generic_get_attribute:
        _LOGGER.debug(f"Patching async_browse_media for generic entities")
        MediaPlayerEntity.orig__getattribute__ = MediaPlayerEntity.__getattribute__
        MediaPlayerEntity.__getattribute__ = _patch_generic_get_attribute


def _uninstall_generic():
    from homeassistant.components.media_player import MediaPlayerEntity

    if MediaPlayerEntity.__getattribute__ is _patch_generic_get_attribute:
        # noinspection PyUnresolvedReferences
        MediaPlayerEntity.__getattribute__ = MediaPlayerEntity.orig__getattribute__


@property
def _patch_supported_features(self: "MediaPlayerEntity") -> int:
    # noinspection PyUnresolvedReferences
    return self.orig_supported_features | SUPPORT_BROWSE_MEDIA


async def async_setup(hass: HomeAssistantType, config: ConfigType) -> bool:
    _LOGGER.debug("Begin component setup")

    domain_config = config.get(DOMAIN)
    if domain_config is None:
        _LOGGER.debug("Configuration is empty")
        return True

    entries = hass.config_entries.async_entries(DOMAIN)
    iter_entries = iter(entries)

    try:
        first_entry = next(iter_entries)

    except StopIteration:
        _LOGGER.debug("Creating new import configuration")
        hass.data[DATA_YAML_CONFIG] = domain_config
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={CONF_SOURCE: SOURCE_IMPORT},
                data={},
            )
        )

    else:
        if first_entry.source == SOURCE_IMPORT:
            _LOGGER.info(f"Loading configuration from YAML: {first_entry.entry_id}")
            hass.data[DATA_YAML_CONFIG] = domain_config
        else:
            _LOGGER.warning(f"Configuration is overridden by a GUI entry: {first_entry.entry_id}")

        for invalid_entry in iter_entries:
            _LOGGER.warning(f"Disabling duplicate entry: {invalid_entry.entry_id}")
            hass.async_create_task(
                hass.config_entries.async_set_disabled_by(
                    invalid_entry.entry_id,
                    disabled_by=DOMAIN,
                )
            )

    _LOGGER.debug("Component setup complete")

    return True


@bind_hass
async def async_setup_entry(hass: HomeAssistantType, config_entry: ConfigEntry) -> bool:
    entry_id = config_entry.entry_id
    _LOGGER.debug(f"Begin entry setup: {entry_id}")

    try:
        if hass.data.get(DATA_BROWSER) is not None:
            raise ConfigEntryNotReady("Another entry is using browser slot")

        if config_entry.source == SOURCE_IMPORT:
            if DATA_YAML_CONFIG not in hass.data:
                _LOGGER.info(f"Removing entry {entry_id} after removal from YAML configuration.")
                hass.async_create_task(hass.config_entries.async_remove(entry_id))
                return False
            config = hass.data[DATA_YAML_CONFIG]
        else:
            config = config_entry.data

        try:
            _LOGGER.debug("Performing installation for entry")
            _install_yandex_station()
        except ImportError:
            _LOGGER.fatal("Yandex Station component is not available")
            return False

        _LOGGER.debug("Performing generic installation for entry")
        _install_generic()

        _LOGGER.debug("Installation complete")

        hass.data[DATA_BROWSER] = None
        hass.data[DOMAIN] = CONFIG_ENTRY_SCHEMA(dict(config))

        return True

    finally:
        _LOGGER.debug(f"End entry setup: {entry_id}")


@bind_hass
async def async_unload_entry(hass: HomeAssistantType, config_entry: ConfigEntry) -> bool:
    _LOGGER.debug(f"Begin entry unload: {config_entry.entry_id}")

    try:
        hass.data[DOMAIN] = None
        hass.data[DATA_BROWSER] = None

        _uninstall_yandex_station()
        _async_update_yandex_entities()

        return True
    finally:
        _LOGGER.debug(f"End entry unload: {config_entry.entry_id}")
