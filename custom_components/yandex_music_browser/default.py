import asyncio
import logging
from typing import Union

import aiohttp
from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.typing import HomeAssistantType
from yandex_music import Client

from custom_components.yandex_music_browser.const import (
    CONF_CREDENTIALS,
    CONF_X_TOKEN,
    DATA_AUTHENTICATORS,
    DATA_BROWSER,
    DOMAIN,
)
from custom_components.yandex_music_browser.media_browser import (
    YandexMusicBrowser,
    YandexMusicBrowserAuthenticationError,
)

_LOGGER = logging.getLogger(__name__)


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


async def async_authenticate_using_config_credentials(hass: HomeAssistantType) -> "Client":
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


async def async_get_music_browser(
    entity: Union[MediaPlayerEntity, HomeAssistantType]
) -> YandexMusicBrowser:
    hass = entity.hass if isinstance(entity, MediaPlayerEntity) else entity

    music_browser = hass.data.get(DATA_BROWSER)

    if isinstance(music_browser, asyncio.Future):
        # Await running authentication process
        music_browser = await music_browser

    elif music_browser is None:
        # Create running authentication process
        future_obj = hass.loop.create_future()
        hass.data[DATA_BROWSER] = future_obj

        try:
            authentication = None

            for patch, authenticator in hass.data[DATA_AUTHENTICATORS].items():
                # Attempt to authenticate using patches
                try:
                    authentication = await authenticator(entity)
                except BaseException as e:
                    _LOGGER.error(f"Patch {patch} failed to authenticate: {e}")

            if authentication is None:
                # Fall back to default authentication methods
                try:
                    authentication = await async_authenticate_using_config_credentials(hass)
                except BaseException as e:
                    _LOGGER.error(f"Default authentication failed: {e}")

                    raise YandexMusicBrowserAuthenticationError(
                        "Could not authenticate with any of the provided patches"
                    )

            # Instantiate music browser object
            music_browser = await hass.async_add_executor_job(
                YandexMusicBrowser,
                authentication,
                hass.data[DOMAIN],
            )

        except BaseException as e:
            # Remove browser future
            hass.data[DATA_BROWSER] = None
            future_obj.set_exception(e)
            raise

        else:
            # Set browser object in place of the future
            hass.data[DATA_BROWSER] = music_browser
            future_obj.set_result(music_browser)

    return music_browser
