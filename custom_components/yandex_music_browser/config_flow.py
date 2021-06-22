from typing import Any, Dict, Mapping, Optional

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import CONN_CLASS_CLOUD_POLL, ConfigFlow

from custom_components.yandex_music_browser.const import CONF_PATCHES, DOMAIN, CONF_DEBUG

PATCH_STATE_VALUES = {
    None: "auto",
    True: "require",
    False: "disable",
}


def _get_main_schema(default_values: Optional[Mapping[str, Any]] = None):
    from custom_components.yandex_music_browser.patches import __all__ as patches_list

    if default_values is None:
        default_values = {}

    patch_values = default_values.get(CONF_PATCHES, {})

    schema_dict = {
        vol.Optional(
            patch,
            default=list(PATCH_STATE_VALUES.keys()).index(patch_values.get(patch)),
        ): vol.In(dict(zip(range(len(PATCH_STATE_VALUES)), PATCH_STATE_VALUES.values())))
        for patch in patches_list
    }

    schema_dict[
        vol.Optional(CONF_DEBUG, default=default_values.get(CONF_DEBUG, False))
    ] = cv.boolean

    return vol.Schema(schema_dict)


class YandexMusicBrowserConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = CONN_CLASS_CLOUD_POLL

    def __init__(self):
        pass

    def _async_create_entry(self, config: Dict[str, Any]) -> Dict[str, Any]:
        current_entries = self._async_current_entries()

        if current_entries:
            return self.async_abort(reason="already_exists")
        return self.async_create_entry(title=DOMAIN, data=config)

    async def async_step_user(
        self,
        user_input: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=_get_main_schema())

        for key in list(user_input.keys()):
            if key != CONF_DEBUG:
                user_input.setdefault(CONF_PATCHES, {})[key] = list(PATCH_STATE_VALUES.keys())[
                    int(user_input.pop(key))
                ]

        return self._async_create_entry(user_input)

    async def async_step_import(
        self,
        user_input: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if user_input is None:
            return self.async_abort(reason="unknown_error")

        return self._async_create_entry(user_input)
