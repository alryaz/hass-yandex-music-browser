__all__ = [
    "config_flow",
    "const",
    "media_browser",
]

import datetime
import logging
from datetime import timedelta
from typing import Any, Final, Mapping, Optional

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.const import *
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType, HomeAssistantType
from homeassistant.loader import bind_hass

from custom_components.yandex_music_browser.const import (
    CONF_CACHE_TTL,
    CONF_CLASS,
    CONF_CREDENTIALS,
    CONF_DEBUG,
    CONF_HEIGHT,
    CONF_IMAGE,
    CONF_ITEMS,
    CONF_LANGUAGE,
    CONF_LYRICS,
    CONF_MENU_OPTIONS,
    CONF_PATCHES,
    CONF_SHOW_HIDDEN,
    CONF_THUMBNAIL_RESOLUTION,
    CONF_TITLE,
    CONF_WIDTH,
    CONF_X_TOKEN,
    DATA_AUTHENTICATORS,
    DATA_BROWSER,
    DATA_CONFIG,
    DATA_UNINSTALLS,
    DATA_UPDATE_LISTENER,
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
    YandexBrowseMedia,
    YandexMusicBrowser,
    YandexMusicBrowserAuthenticationError,
    YandexMusicBrowserException,
    sanitize_media_link,
)

_LOGGER = logging.getLogger(__name__)


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

DEFAULT_LANGUAGE: Final = "ru" if IS_IN_RUSSIA else "en"

PATCHES_SCHEMA: Optional[vol.Schema] = None


def lazy_load_patches_schema(value: Any):
    global PATCHES_SCHEMA
    if PATCHES_SCHEMA is None:
        from custom_components.yandex_music_browser.patches import __all__ as patches_list

        PATCHES_SCHEMA = vol.Schema(
            {
                vol.Optional(patch, default=None): vol.Any(vol.Equal(None), cv.boolean)
                for patch in patches_list
            }
        )

    return PATCHES_SCHEMA(value)


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
        vol.Optional(
            CONF_PATCHES, default=lambda: lazy_load_patches_schema({})
        ): lazy_load_patches_schema,
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
            config = CONFIG_ENTRY_SCHEMA(dict(config_entry.data))

        uninstalls = {}
        authenticators = {}
        from importlib import import_module

        for patch_installing, is_enabled in config.get(CONF_PATCHES, {}).items():
            if is_enabled is True or is_enabled is None:
                try:
                    patch_module = import_module(
                        f"custom_components.{DOMAIN}.patches.{patch_installing}"
                    )
                except ImportError as e:
                    _LOGGER.error(f"Could not import patch {patch_installing}: {e}")
                    return False

                (install, uninstall, async_authenticate) = (
                    patch_module.install,
                    patch_module.uninstall,
                    getattr(patch_module, "async_authenticate", None),
                )

                uninstalls[patch_installing] = uninstall

                try:
                    _LOGGER.info(f"Installing patch: {patch_installing}")
                    install(hass)

                except BaseException as e:
                    if is_enabled is None:
                        try:
                            uninstall(hass)
                        except BaseException as e:
                            _LOGGER.error(
                                f"Could not post-error uninstall patch {patch_installing}: {e}"
                            )
                        del uninstalls[patch_installing]
                        continue
                    else:
                        for patch_uninstalling, uninstall in uninstalls.items():
                            try:
                                uninstall(hass)
                            except BaseException as e:
                                _LOGGER.error(
                                    f"Could not post-error uninstall patch {patch_uninstalling}: {e}"
                                )
                        return False

                if async_authenticate:
                    authenticators[patch_installing] = async_authenticate

        if not uninstalls:
            _LOGGER.warning("No patches enabled, component will shut down")
            return False

        _LOGGER.debug("Installation complete")

        hass.data[DATA_AUTHENTICATORS] = authenticators
        hass.data[DATA_UNINSTALLS] = uninstalls
        hass.data[DATA_BROWSER] = None
        hass.data[DOMAIN] = CONFIG_ENTRY_SCHEMA(dict(config))

        return True

    finally:
        _LOGGER.debug(f"End entry setup: {entry_id}")


@bind_hass
async def async_unload_entry(hass: HomeAssistantType, config_entry: ConfigEntry) -> bool:
    _LOGGER.debug(f"Begin entry unload: {config_entry.entry_id}")

    hass.data[DOMAIN] = None
    hass.data[DATA_BROWSER] = None

    del hass.data[DATA_AUTHENTICATORS]

    uninstalls = hass.data.pop(DATA_UNINSTALLS)

    for patch_uninstalling, uninstall in uninstalls.items():
        try:
            _LOGGER.info(f"Uninstalling patch: {patch_uninstalling}")
            uninstall(hass)
        except BaseException as e:
            _LOGGER.error(f"Could not post-error uninstall patch {patch_uninstalling}: {e}")
            return False

    _LOGGER.debug(f"End entry unload: {config_entry.entry_id}")
    return True
