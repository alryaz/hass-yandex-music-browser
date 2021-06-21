__all__ = [
    "config_flow",
    "const",
    "media_browser",
]

import asyncio
import datetime
import logging
from datetime import timedelta
from typing import Any, Final, Mapping, MutableMapping, Optional, TYPE_CHECKING, Type

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    DOMAIN,
    SUPPORT_BROWSE_MEDIA,
)
from homeassistant.components.media_player.const import (
    MEDIA_TYPE_ALBUM,
    MEDIA_TYPE_PLAYLIST,
    MEDIA_TYPE_TRACK,
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
    YandexMusicBrowser,
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
    }
)

CONFIG_SCHEMA: Final = vol.Schema(
    {
        vol.Optional(DOMAIN): CONFIG_ENTRY_SCHEMA,
    },
    extra=vol.ALLOW_EXTRA,
)


def get_yandex_media_player_class() -> Optional[Type["YandexStation"]]:
    try:
        from custom_components.yandex_station.media_player import YandexStation

        return YandexStation
    except ImportError:
        return None


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
            _install()
        except ImportError:
            _LOGGER.fatal("Yandex Station component is not available")
            return False

        _async_update_yandex_entities()
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

        _uninstall()
        _async_update_yandex_entities()

        return True
    finally:
        _LOGGER.debug(f"End entry unload: {config_entry.entry_id}")


async def _patch_async_play_media(self: "YandexStation", media_type: str, media_id: str, **kwargs):
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
    return await self.orig_async_play_media(media_type, media_id, **kwargs)


@callback
def _async_update_yandex_entities():
    from custom_components.yandex_station.media_player import YandexStation
    import gc

    for obj in gc.get_objects():
        if isinstance(obj, YandexStation) and obj.hass is not None and obj.enabled and obj._added:
            obj.async_schedule_update_ha_state(force_refresh=True)


def _install():
    from custom_components.yandex_station.media_player import YandexStation

    if YandexStation.async_browse_media is not _patch_async_browse_media:
        _LOGGER.debug(f"Patching async_browse_media")
        YandexStation.orig_browse_media = YandexStation.async_browse_media
        YandexStation.async_browse_media = _patch_async_browse_media

    if YandexStation.async_play_media is not _patch_async_play_media:
        _LOGGER.debug(f"Patching async_play_media")
        YandexStation.orig_async_play_media = YandexStation.async_play_media
        YandexStation.async_play_media = _patch_async_play_media

    if YandexStation.supported_features is not _patch_supported_features:
        _LOGGER.debug(f"Patching supported_features")
        YandexStation.orig_supported_features = YandexStation.supported_features
        # noinspection PyPropertyAccess
        YandexStation.supported_features = _patch_supported_features


def _uninstall():
    from custom_components.yandex_station.media_player import YandexStation

    if YandexStation.async_browse_media is _patch_async_browse_media:
        YandexStation.async_browse_media = YandexStation.orig_browse_media

    if YandexStation.async_play_media is _patch_async_play_media:
        YandexStation.async_play_media = YandexStation.async_play_media

    if YandexStation.supported_features is _patch_supported_features:
        # noinspection PyPropertyAccess
        YandexStation.supported_features = _patch_supported_features


async def _patch_async_browse_media(
    self: "YandexStation",
    media_content_type: Optional[str] = None,
    media_content_id: Optional[str] = None,
) -> BrowseMedia:
    registry_entry = self.registry_entry
    if registry_entry is None:
        raise BrowseError(f"registry entry does not exist")

    config_entry_id = registry_entry.config_entry_id
    if config_entry_id is None:
        raise BrowseError(f"config entry does not exist")

    hass = self.hass
    music_browser = hass.data.get(DATA_BROWSER)

    if isinstance(music_browser, asyncio.Future):
        music_browser = await music_browser

    elif music_browser is None:
        future_obj = hass.loop.create_future()
        hass.data[DATA_BROWSER] = future_obj

        try:
            session = self.quasar.session
            music_token = session.music_token
            if music_token is None:
                x_token = session.x_token
                if x_token is None:
                    raise ValueError("x_token is empty!")
                music_token = await session.get_music_token(x_token)
                session.music_token = music_token

            music_browser = await hass.async_add_executor_job(
                YandexMusicBrowser,
                music_token,
                hass.data[DOMAIN],
            )

        except BaseException as e:
            future_obj.set_exception(e)
            raise
        else:
            hass.data[DATA_BROWSER] = music_browser
            future_obj.set_result(music_browser)

    if media_content_type is None:
        media_content_type = ROOT_MEDIA_CONTENT_TYPE

    self.debug("Requesting browse: %s / %s" % (media_content_type, media_content_id))
    response = await self.hass.async_add_executor_job(
        music_browser.generate_browse_from_media,
        (media_content_type, media_content_id),
        True,  # fetch_children
        True,  # cache_garbage_collection
    )

    if response is None:
        _LOGGER.debug("Media type: %s", type(media_content_type))
        raise BrowseError(f"Media not found: {media_content_type} / {media_content_id}")

    return _update_browse_object_for_cloud(
        music_browser,
        response,
        for_cloud=not self.local_state,
    )


@property
def _patch_supported_features(self: "YandexStation") -> int:
    # noinspection PyUnresolvedReferences
    return self.orig_supported_features | SUPPORT_BROWSE_MEDIA


def _update_browse_object_for_cloud(
    music_browser: "YandexMusicBrowser",
    browse_object: BrowseMedia,
    for_cloud: bool = True,
) -> BrowseMedia:
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
            _update_browse_object_for_cloud(music_browser, child, for_cloud=for_cloud)

    return browse_object
