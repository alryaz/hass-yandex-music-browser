from typing import Any, Dict, Optional
import voluptuous as vol

from homeassistant.config_entries import CONN_CLASS_CLOUD_POLL, ConfigFlow

from custom_components.yandex_music_browser.const import DOMAIN
from custom_components.yandex_station import CONF_DEBUG

import homeassistant.helpers.config_validation as cv


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
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Optional(CONF_DEBUG, default=False): cv.boolean,
                    }
                ),
            )

        return self._async_create_entry(user_input)

    async def async_step_import(
        self,
        user_input: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if user_input is None:
            return self.async_abort(reason="unknown_error")

        return self._async_create_entry(user_input)
