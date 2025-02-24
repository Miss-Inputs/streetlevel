import itertools
from typing import List, Optional
from PIL import Image
from aiohttp import ClientSession
from requests import Session

from streetlevel.geo import *
from .panorama import StreetViewPanorama, LocalizedString, CaptureDate, BuildingLevel, UploadDate
from .depth import parse as parse_depth
from . import api
from ..dataclasses import Size, Tile, Link
from ..util import try_get, get_equirectangular_panorama, get_equirectangular_panorama_async
from .util import is_third_party_panoid


def find_panorama(lat: float, lon: float, radius: int = 50, locale: str = "en",
                  search_third_party: bool = False, session: Session = None) -> Optional[StreetViewPanorama]:
    """
    Searches for a panorama within a radius around a point.

    :param lat: Latitude of the center point.
    :param lon: Longitude of the center point.
    :param radius: *(optional)* Search radius in meters. Defaults to 50.
    :param locale: *(optional)* Desired language of the location's address as IETF code.
      Defaults to ``en``.
    :param search_third_party: *(optional)* Whether to search for third-party panoramas
      rather than official ones. Defaults to false.
    :param session: *(optional)* A requests session.
    :return: A StreetViewPanorama object if a panorama was found, or None.
    """

    # TODO
    # the `SingleImageSearch` call returns a different kind of depth data
    # than `photometa`; need to deal with that at some point

    resp = api.find_panorama_raw(lat, lon, radius=radius, download_depth=False,
                                 locale=locale, search_third_party=search_third_party, session=session)

    response_code = resp[0][0][0]
    # 0: OK
    # 5: search returned no images
    # don't know if there are others
    if response_code != 0:
        return None

    pano = _parse_pano_message(resp[0][1])
    return pano


async def find_panorama_async(lat: float, lon: float, session: ClientSession, radius: int = 50,
                              locale: str = "en", search_third_party: bool = False) -> Optional[StreetViewPanorama]:
    # TODO
    # the `SingleImageSearch` call returns a different kind of depth data
    # than `photometa`; need to deal with that at some point
    resp = await api.find_panorama_raw_async(lat, lon, session, radius=radius, download_depth=False,
                                             locale=locale, search_third_party=search_third_party)

    response_code = resp[0][0][0]
    # 0: OK
    # 5: search returned no images
    # don't know if there are others
    if response_code != 0:
        return None

    pano = _parse_pano_message(resp[0][1])
    return pano


def find_panorama_by_id(panoid: str, download_depth: bool = False, locale: str = "en",
                        session: Session = None) -> Optional[StreetViewPanorama]:
    """
    Fetches metadata of a specific panorama.

    Unfortunately, `as mentioned on this page
    <https://developers.google.com/maps/documentation/tile/streetview#panoid_response>`_,
    pano IDs are not stable, so a request that works today may return nothing a few months into the future.

    :param panoid: The pano ID.
    :param download_depth: Whether to download and parse the depth map.
    :param locale: Desired language of the location's address as IETF code.
    :param session: *(optional)* A requests session.
    :return: A StreetViewPanorama object if a panorama with this ID exists, or None.
    """
    resp = api.find_panorama_by_id_raw(panoid, download_depth=download_depth,
                                       locale=locale, session=session)

    response_code = resp[1][0][0][0]
    # 1: OK
    # 2: Not found
    # don't know if there are others
    if response_code != 1:
        return None

    pano = _parse_pano_message(resp[1][0])
    return pano


async def find_panorama_by_id_async(panoid: str, session: ClientSession, download_depth: bool = False,
                                    locale: str = "en") -> Optional[StreetViewPanorama]:
    resp = await api.find_panorama_by_id_raw_async(panoid, session, download_depth=download_depth, locale=locale)

    response_code = resp[1][0][0][0]
    # 1: OK
    # 2: Not found
    # don't know if there are others
    if response_code != 1:
        return None

    pano = _parse_pano_message(resp[1][0])
    return pano


def get_coverage_tile(tile_x: int, tile_y: int, session: Session = None) -> List[StreetViewPanorama]:
    """
    Fetches Street View coverage on a specific map tile. Coordinates are in Slippy Map aka XYZ format
    at zoom level 17.

    When viewing Google Maps with satellite imagery in globe view and zooming into a spot,
    it makes this API call. This is useful because 1) it allows for fetching coverage for a whole area, and
    2) there are various hidden/removed locations which cannot be found by any other method
    (unless you access them by pano ID directly).

    Note, however, that only ID, latitude and longitude of the most recent coverage are returned.
    The rest of the metadata, as well as historical panoramas, must be fetched manually one by one.

    :param tile_x: X coordinate of the tile.
    :param tile_y: Y coordinate of the tile.
    :param session: *(optional)* A requests session.
    :return: A list of StreetViewPanoramas. If no coverage was returned by the API, the list is empty.
    """
    resp = api.get_coverage_tile_raw(tile_x, tile_y, session)

    if resp is None:
        return []

    return _parse_coverage_tile_response(resp)


async def get_coverage_tile_async(tile_x: int, tile_y: int, session: ClientSession) -> List[StreetViewPanorama]:
    resp = await api.get_coverage_tile_raw_async(tile_x, tile_y, session)

    if resp is None:
        return []

    return _parse_coverage_tile_response(resp)


def get_coverage_tile_by_latlon(lat: float, lon: float, session: Session = None) -> List[StreetViewPanorama]:
    """
    Same as :func:`get_coverage_tile <get_coverage_tile>`, but for fetching the tile on which a point is located.

    :param lat: Latitude of the point.
    :param lon: Longitude of the point.
    :param session: *(optional)* A requests session.
    :return: A list of StreetViewPanoramas. If no coverage was returned by the API, the list is empty.
    """
    tile_coord = wgs84_to_tile_coord(lat, lon, 17)
    return get_coverage_tile(tile_coord[0], tile_coord[1], session=session)


async def get_coverage_tile_by_latlon_async(lat: float, lon: float, session: ClientSession) \
        -> List[StreetViewPanorama]:
    tile_coord = wgs84_to_tile_coord(lat, lon, 17)
    return await get_coverage_tile_async(tile_coord[0], tile_coord[1], session)


def download_panorama(pano: StreetViewPanorama, path: str, zoom: int = 5, pil_args: dict = None) -> None:
    """
    Downloads a panorama to a file.

    :param pano: The panorama to download.
    :param path: Output path.
    :param zoom: *(optional)* Image size; 0 is lowest, 5 is highest. The dimensions of a zoom level of a
        specific panorama depend on the camera used. If the requested zoom level does not exist,
        the highest available level will be downloaded. Defaults to 5.
    :param pil_args: *(optional)* Additional arguments for PIL's
        `Image.save <https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.save>`_
        method, e.g. ``{"quality":100}``. Defaults to ``{}``.
    """
    if pil_args is None:
        pil_args = {}
    image = get_panorama(pano, zoom=zoom)
    image.save(path, **pil_args)


async def download_panorama_async(pano: StreetViewPanorama, path: str, session: ClientSession,
                                  zoom: int = 5, pil_args: dict = None) -> None:
    if pil_args is None:
        pil_args = {}
    image = await get_panorama_async(pano, session, zoom=zoom)
    image.save(path, **pil_args)


def get_panorama(pano: StreetViewPanorama, zoom: int = 5) -> Image.Image:
    """
    Downloads a panorama and returns it as PIL image.

    :param pano: The panorama to download.
    :param zoom: *(optional)* Image size; 0 is lowest, 5 is highest. The dimensions of a zoom level of a
        specific panorama depend on the camera used. If the requested zoom level does not exist,
        the highest available level will be downloaded. Defaults to 5.
    :return: A PIL image containing the panorama.
    """
    zoom = _validate_get_panorama_params(pano, zoom)
    return get_equirectangular_panorama(
        pano.image_sizes[zoom].x, pano.image_sizes[zoom].y,
        pano.tile_size, _generate_tile_list(pano, zoom))


async def get_panorama_async(pano: StreetViewPanorama, session: ClientSession, zoom: int = 5) -> Image.Image:
    zoom = _validate_get_panorama_params(pano, zoom)
    return await get_equirectangular_panorama_async(
        pano.image_sizes[zoom].x, pano.image_sizes[zoom].y,
        pano.tile_size, _generate_tile_list(pano, zoom),
        session)


def _validate_get_panorama_params(pano, zoom):
    if not pano.image_sizes:
        raise ValueError("pano.image_sizes is None.")
    zoom = max(0, min(zoom, len(pano.image_sizes) - 1))
    return zoom


def _parse_coverage_tile_response(tile):
    panos = []
    if tile[1] is not None and len(tile[1]) > 0:
        for pano in tile[1][1]:
            if pano[0][0] == 1:
                continue
            panoid = pano[0][0][1]
            lat = pano[0][2][0][2]
            lon = pano[0][2][0][3]
            panos.append(StreetViewPanorama(panoid, lat, lon))
    return panos


def _parse_pano_message(msg: dict) -> StreetViewPanorama:
    img_sizes = msg[2][3][0]
    img_sizes = list(map(lambda x: Size(x[0][1], x[0][0]), img_sizes))
    others = try_get(lambda: msg[5][0][3][0])
    date = msg[6][7]

    links, other_bld_levels, other_dates = _parse_other_pano_indices(msg)

    street_name = try_get(lambda: msg[5][0][12][0][0][0][2])
    if street_name is not None:
        street_name = LocalizedString(street_name[0], street_name[1])

    address = try_get(lambda: msg[3][2])
    if address is not None:
        address = [LocalizedString(x[0], x[1]) for x in address]

    depth = try_get(lambda: msg[5][0][5][1][2])
    if depth:
        depth = parse_depth(depth)

    upload_date = try_get(lambda: msg[6][8])
    if upload_date:
        upload_date = UploadDate(*upload_date)

    pano = StreetViewPanorama(
        id=msg[1][1],
        lat=msg[5][0][1][0][2],
        lon=msg[5][0][1][0][3],
        heading=try_get(lambda: math.radians(msg[5][0][1][2][0])),
        pitch=try_get(lambda: math.radians(90 - msg[5][0][1][2][1])),
        roll=try_get(lambda: math.radians(msg[5][0][1][2][2])),
        depth=depth,
        date=CaptureDate(date[0],
                         date[1],
                         date[2] if len(date) > 2 else None),
        upload_date=upload_date,
        elevation=try_get(lambda: msg[5][0][1][1][0]),
        tile_size=Size(msg[2][3][1][0], msg[2][3][1][1]),
        image_sizes=img_sizes,
        source=try_get(lambda: msg[6][5][2].lower()),
        country_code=try_get(lambda: msg[5][0][1][4]),
        street_name=street_name,
        address=address,
        copyright_message=try_get(lambda: msg[4][0][0][0][0]),
        uploader=try_get(lambda: msg[4][1][0][0][0]),
        uploader_icon_url=try_get(lambda: msg[4][1][0][2]),
        building_level=_parse_building_level_message(try_get(lambda: msg[5][0][1][3])),
    )

    # parse other dates, links and neighbors
    if others is not None:
        for idx, other in enumerate(others):
            other_id = other[0][1]
            if pano.id == other_id:
                continue

            connected = StreetViewPanorama(
                id=other_id,
                lat=try_get(lambda: float(other[2][0][2])),
                lon=try_get(lambda: float(other[2][0][3])),
                elevation=try_get(lambda: other[2][1][0]),
                heading=try_get(lambda: math.radians(other[2][2][0])),
                pitch=try_get(lambda: math.radians(90 - other[2][2][1])),
                roll=try_get(lambda: math.radians(other[2][2][2])),
                building_level=_parse_building_level_message(try_get(lambda: other[2][3])),
            )

            if idx in other_dates:
                connected.date = CaptureDate(other_dates[idx][1], other_dates[idx][0])
                pano.historical.append(connected)
            else:
                if idx in links:
                    if links[idx]:
                        angle = math.radians(links[idx][3])
                    else:
                        angle = get_bearing(pano.lat, pano.lon, connected.lat, connected.lon)
                    pano.links.append(Link(connected, angle))
                if idx in other_bld_levels:
                    pano.building_levels.append(connected)
                pano.neighbors.append(connected)

            connected.street_name = try_get(lambda: other[3][2][0])
    pano.historical = sorted(pano.historical, key=lambda x: (x.date.year, x.date.month), reverse=True)

    return pano


def _parse_other_pano_indices(msg: dict) -> Tuple[dict, list, dict]:
    links_raw = try_get(lambda: msg[5][0][6])
    if links_raw:
        links = dict([(x[0], try_get(lambda: x[1])) for x in links_raw])
    else:
        links = {}

    other_bld_levels_raw = try_get(lambda: msg[5][0][7])
    if other_bld_levels_raw:
        other_bld_levels = [x[0] for x in other_bld_levels_raw]
    else:
        other_bld_levels = []

    other_dates_raw = try_get(lambda: msg[5][0][8])
    if other_dates_raw:
        other_dates = dict([(x[0], x[1]) for x in other_dates_raw])
    else:
        other_dates = {}
    return links, other_bld_levels, other_dates


def _parse_building_level_message(bld_level: Optional[list]) -> Optional[BuildingLevel]:
    if bld_level and len(bld_level) > 1:
        return BuildingLevel(
            bld_level[1],
            LocalizedString(*bld_level[2]),
            LocalizedString(*bld_level[3]))
    return None


def _generate_tile_list(pano: StreetViewPanorama, zoom: int) -> List[Tile]:
    """
    Generates a list of a panorama's tiles and the URLs pointing to them.
    """
    img_size = pano.image_sizes[zoom]
    tile_width = pano.tile_size.x
    tile_height = pano.tile_size.y
    cols = math.ceil(img_size.x / tile_width)
    rows = math.ceil(img_size.y / tile_height)

    IMAGE_URL = "https://cbk0.google.com/cbk?output=tile&panoid={0:}&zoom={3:}&x={1:}&y={2:}"
    THIRD_PARTY_IMAGE_URL = "https://lh3.ggpht.com/p/{0:}=x{1:}-y{2:}-z{3:}"

    url_to_use = THIRD_PARTY_IMAGE_URL if is_third_party_panoid(pano.id) else IMAGE_URL

    coords = list(itertools.product(range(cols), range(rows)))
    tiles = [Tile(x, y, url_to_use.format(pano.id, x, y, zoom)) for x, y in coords]
    return tiles
