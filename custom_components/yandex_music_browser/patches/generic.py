import logging
from functools import wraps
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Type, TypeVar, Union
from urllib.parse import urlencode

from aiohttp.abc import Request
from aiohttp.web_exceptions import HTTPFound
from aiohttp.web_response import Response
from homeassistant.components.http import HomeAssistantView, KEY_HASS
from homeassistant.components.media_player import (
    BrowseError,
    MediaPlayerEntity,
    SUPPORT_BROWSE_MEDIA,
    SUPPORT_PLAY_MEDIA,
)
from homeassistant.helpers.typing import HomeAssistantType
from m3u8_generator import PlaylistGenerator
from yandex_music import DownloadInfo, Playlist, Track, YandexMusicObject

from custom_components.yandex_music_browser.const import DOMAIN, ROOT_MEDIA_CONTENT_TYPE
from custom_components.yandex_music_browser.default import async_get_music_browser
from custom_components.yandex_music_browser.media_browser import (
    YandexBrowseMedia,
    YandexMusicBrowser,
    YandexMusicBrowserException,
)
from custom_components.yandex_music_browser.patches._base import _patch_root_async_browse_media

_LOGGER = logging.getLogger(__name__)


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
        browse_object = await _patch_root_async_browse_media(self, media_type, media_id)
        media_object = getattr(browse_object, "media_object", None)

        if media_object:
            # Check if media object is supported for URL generation
            media_object_type = type(media_object)
            if media_object_type in URL_ITEM_VALIDATORS:

                # Retrieve URL parser
                getter, _ = URL_ITEM_VALIDATORS[media_object_type]
                media_id = None

                if getattr(getter, "_is_urls_container", False):
                    internal_url = self.hass.config.internal_url
                    if internal_url is not None:
                        media_id = (
                            internal_url
                            + YandexMusicBrowserView.url
                            + "?"
                            + urlencode(
                                {
                                    "key": "1",
                                    "type": browse_object.yandex_media_content_type,
                                    "id": browse_object.yandex_media_content_id,
                                }
                            )
                        )

                else:
                    # Allow playback only if no test is provided, or preliminary test succeeds
                    media_id = await self.hass.async_add_executor_job(
                        getter, self.hass, media_object
                    )

                if media_id:
                    # Redirect
                    _LOGGER.debug("Retrieved URL: %s", media_id)
                    return await object.__getattribute__(self, "async_play_media")(
                        media_id=media_id,
                        media_type="audio",
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
        yandex_browse_object = await _patch_root_async_browse_media(
            self, media_content_type, media_content_id, fetch_children=True
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
            yandex_browse_object = await _patch_root_async_browse_media(
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
            self.hass,
            await async_get_music_browser(self),
            yandex_browse_object,
        )

    return result_object


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


#################################################################################
# URL Filtering and processing
#################################################################################


def _update_browse_object_for_url(
    hass: HomeAssistantType,
    music_browser: "YandexMusicBrowser",
    browse_object: YandexBrowseMedia,
) -> YandexBrowseMedia:
    browse_object.media_content_type = "yandex"
    browse_object.media_content_id = (
        browse_object.yandex_media_content_type + ":" + browse_object.yandex_media_content_id
    )

    if browse_object.children:
        browse_object.children = list(
            map(
                lambda x: _update_browse_object_for_url(hass, music_browser, x),
                browse_object.children,
            )
        )

    media_object = browse_object.media_object

    can_play = False
    if media_object:
        solver = URL_ITEM_VALIDATORS.get(media_object.__class__)
        if solver:
            url_getter, requires_test = solver
            if requires_test is False:
                can_play = True
            else:
                can_play = bool(url_getter(hass, media_object))

    browse_object.can_play = can_play

    return browse_object


class YandexMusicBrowserView(HomeAssistantView):
    """Handle Yandex Smart Home unauthorized requests."""

    url = "/api/yandex_music_browser/v1.0/playlist.m3u8"
    name = "api:yandex_music_browser"
    requires_auth = False

    async def get(self, request: Request) -> Response:
        """Handle Yandex Smart Home HEAD requests."""
        hass: HomeAssistantType = request.app[KEY_HASS]

        # Bind to existence of config within HA data
        if DOMAIN not in hass.data:
            return Response(status=404)

        # Retrieve required query parameters
        key = request.query.get("key")
        type_ = request.query.get("type")
        id_ = request.query.get("id")

        # Check required query parameters fullness
        if not (key and type_ and id_):
            return Response(status=400, body="key or media key not provided")

        # Get browse media object
        try:
            browse_object = await _patch_root_async_browse_media(
                hass, type_, id_, fetch_children=False
            )
        except BrowseError as e:
            return Response(status=404, body=str(e))

        media_object = browse_object.media_object
        if media_object is None:
            return Response(status=404)

        validator = URL_ITEM_VALIDATORS.get(media_object.__class__)
        if validator is None:
            return Response(status=404)

        url_getter, _ = validator

        urls = await hass.async_add_executor_job(url_getter, hass, media_object)
        if urls is None:
            return Response(status=404)

        if isinstance(urls, str):
            return HTTPFound(urls)

        # @TODO: if media.can_play:

        m3u8str = "#EXTM3U\n\n"
        for i, url in enumerate(urls, start=1):
            m3u8str += f"#EXTINF: 1, Track {i}\n{url}\n"

        return Response(status=200, body=m3u8str, content_type="application/x-mpegURL")


_TYandexMusicObject = TypeVar("_TYandexMusicObject", bound=YandexMusicObject)
TURLGetter = Callable[[HomeAssistantType, _TYandexMusicObject], Optional[Union[str, Sequence[str]]]]


URL_ITEM_VALIDATORS: Dict[Type[YandexMusicObject], Tuple[TURLGetter, bool]] = {}


def register_url_processor(cls: Type[_TYandexMusicObject], requires_test: bool = True):
    def _wrapper(fn: TURLGetter):
        URL_ITEM_VALIDATORS[cls] = (fn, requires_test)
        return fn

    return _wrapper


def wrap_urls_container(
    fn: Callable[[HomeAssistantType, _TYandexMusicObject], Optional[Sequence[Tuple[str, str]]]]
):
    @wraps(fn)
    def _wrapped(hass: HomeAssistantType, media_object: _TYandexMusicObject):
        internal_url = hass.config.internal_url
        if internal_url is None:
            _LOGGER.debug("To use track containers, you must set your Home Assistant internal URL")
            return None

        items = fn(hass, media_object)
        if items is None:
            return None

        return [
            hass.config.internal_url
            + YandexMusicBrowserView.url
            + "?"
            + urlencode(
                {
                    "key": "anything",  # @TODO: change this
                    "type": type_,
                    "id": id_,
                }
            )
            for type_, id_ in items
        ]

    setattr(_wrapped, "_is_urls_container", True)

    return _wrapped


@register_url_processor(Track, False)
def get_track_play_url(
    hass: HomeAssistantType, media_object: Track, codec: str = "mp3", bitrate_in_kbps: int = 192
) -> Optional[Tuple[str, float]]:
    download_info: Optional[List[DownloadInfo]] = media_object.download_info
    if download_info is None:
        download_info = media_object.get_download_info()

    for info in download_info:
        if info.codec == codec and info.bitrate_in_kbps == bitrate_in_kbps:
            direct_link: Optional[str] = info.direct_link
            if direct_link is None:
                direct_link = info.get_direct_link()
            return direct_link

    return None


@register_url_processor(Playlist)
@wrap_urls_container
def get_playlist_play_url(
    hass: HomeAssistantType,
    media_object: Playlist,
) -> Sequence[Tuple[str, str]]:
    tracks = media_object.tracks
    if tracks is None:
        tracks = media_object.fetch_tracks()

    return [("track", str(track.id)) for track in tracks]


def install(hass: HomeAssistantType):
    from homeassistant.components.media_player import MediaPlayerEntity

    if MediaPlayerEntity.__getattribute__ is not _patch_generic_get_attribute:
        _LOGGER.debug(f"Patching async_browse_media for generic entities")
        MediaPlayerEntity.orig__getattribute__ = MediaPlayerEntity.__getattribute__
        MediaPlayerEntity.__getattribute__ = _patch_generic_get_attribute

    hass.http.register_view(YandexMusicBrowserView())


def uninstall(_: HomeAssistantType):
    from homeassistant.components.media_player import MediaPlayerEntity

    if MediaPlayerEntity.__getattribute__ is _patch_generic_get_attribute:
        # noinspection PyUnresolvedReferences
        MediaPlayerEntity.__getattribute__ = MediaPlayerEntity.orig__getattribute__
