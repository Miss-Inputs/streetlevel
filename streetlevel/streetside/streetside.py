import asyncio
from datetime import datetime
from io import BytesIO
from typing import List, Union, Optional

from PIL import Image
from aiohttp import ClientSession
from requests import Session

from .panorama import StreetsidePanorama
from streetlevel.geo import *
from . import api
from .util import to_base4
from ..util import download_files_async, stitch_cubemap_faces, CubemapStitchingMethod, save_cubemap_panorama

TILE_SIZE = 256


def find_panorama_by_id(panoid: int, session: Session = None) -> Optional[StreetsidePanorama]:
    """
    Fetches metadata for a specific panorama.

    :param panoid: The pano ID.
    :param session: *(optional)* A requests session.
    :return: A StreetsidePanorama object if a panorama was found, or None.
    """
    response = api.find_panorama_by_id_raw(panoid, session)
    if len(response) < 2:
        return None
    pano = _parse_pano(response[1])
    return pano


async def find_panorama_by_id_async(panoid: int, session: ClientSession) -> Optional[StreetsidePanorama]:
    response = await api.find_panorama_by_id_raw_async(panoid, session)
    if len(response) < 2:
        return None
    pano = _parse_pano(response[1])
    return pano


def find_panoramas_in_bbox(north: float, west: float, south: float, east: float,
                           limit: int = 50, session: Session = None) -> List[StreetsidePanorama]:
    """
    Retrieves panoramas within a bounding box.

    :param north: lat1.
    :param west: lon1.
    :param south: lat2.
    :param east: lon2.
    :param limit: *(optional)* Maximum number of results to return. Defaults to 50.
    :param session: *(optional)* A requests session.
    :return: A list of StreetsidePanorama objects.
    """
    response = api.find_panoramas_raw(north, west, south, east, limit, session)
    panos = _parse_panos(response)
    return panos


async def find_panoramas_in_bbox_async(north: float, west: float, south: float, east: float,
                                       session: ClientSession, limit: int = 50) -> List[StreetsidePanorama]:
    response = await api.find_panoramas_raw_async(north, west, south, east, session, limit)
    panos = _parse_panos(response)
    return panos


def find_panoramas(lat: float, lon: float, radius: float = 25,
                   limit: int = 50, session: Session = None) -> List[StreetsidePanorama]:
    """
    Retrieves panoramas within a square around a point.

    :param lat: Latitude of the center point.
    :param lon: Longitude of the center point.
    :param radius: *(optional)* Radius of the square in meters. (Not sure if that's the correct mathematical
      term, but you get the idea.) Defaults to 25.
    :param limit: *(optional)* Maximum number of results to return. Defaults to 50.
    :param session: *(optional)* A requests session.
    :return: A list of StreetsidePanorama objects.
    """
    top_left, bottom_right = create_bounding_box_around_point(lat, lon, radius)
    return find_panoramas_in_bbox(
        top_left[1], top_left[0],
        bottom_right[1], bottom_right[0],
        limit=limit, session=session)


async def find_panoramas_async(lat: float, lon: float, session: ClientSession,
                               radius: float = 25, limit: int = 50) -> List[StreetsidePanorama]:

    top_left, bottom_right = create_bounding_box_around_point(lat, lon, radius)
    return await find_panoramas_in_bbox_async(
        top_left[1], top_left[0],
        bottom_right[1], bottom_right[0],
        session, limit=limit)


def download_panorama(pano: StreetsidePanorama, path: str, zoom: int = 4,
                      stitching_method: CubemapStitchingMethod = CubemapStitchingMethod.ROW,
                      pil_args: dict = None) -> None:
    """
    Downloads a panorama to a file.

    :param pano: The panorama.
    :param path: Output path.
    :param zoom: *(optional)* Image size; 0 is lowest, 4 is highest. Defaults to 4. If 4 is not available, 3 will be
      downloaded.
      (Note that only the old Microsoft panoramas go up to 4; the TomTom-provided panoramas stop at 3.)
    :param stitching_method: *(optional)* Whether and how the faces of the cubemap are stitched into one
        image. Defaults to ``ROW``.
    :param pil_args: *(optional)* Additional arguments for PIL's
        `Image.save <https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.save>`_
        method, e.g. ``{"quality":100}``. Defaults to ``{}``.
    """
    if pil_args is None:
        pil_args = {}

    output = get_panorama(pano, zoom=zoom, stitching_method=stitching_method)
    save_cubemap_panorama(output, path, pil_args)


async def download_panorama_async(pano: StreetsidePanorama, path: str, session: ClientSession, zoom: int = 4,
                                  stitching_method: CubemapStitchingMethod = CubemapStitchingMethod.ROW,
                                  pil_args: dict = None) -> None:
    if pil_args is None:
        pil_args = {}

    output = await get_panorama_async(pano, session, zoom=zoom, stitching_method=stitching_method)
    save_cubemap_panorama(output, path, pil_args)


def get_panorama(pano: StreetsidePanorama, zoom: int = 4,
                 stitching_method: CubemapStitchingMethod = CubemapStitchingMethod.ROW) \
        -> Union[List[Image.Image], Image.Image]:
    """
    Downloads a panorama and returns it as PIL image.

    :param pano: The panorama.
    :param zoom: *(optional)* Image size; 0 is lowest, 4 is highest. Defaults to 4. If 4 is not available, 3 will be
      downloaded.
      (Note that only the old Microsoft panoramas go up to 4; the TomTom-provided panoramas stop at 3.)
    :param stitching_method: *(optional)* Whether and how the faces of the cubemap are stitched into one
        image. Defaults to ``ROW``.
    :return: A PIL image or a list of six PIL images depending on ``stitching_method``.
    """
    zoom = max(0, min(zoom, pano.max_zoom))
    faces = _generate_tile_list(pano.id, zoom)
    _download_tiles(faces)
    return _stitch_panorama(faces, stitching_method=stitching_method)


async def get_panorama_async(pano: StreetsidePanorama, session: ClientSession, zoom: int = 4,
                             stitching_method: CubemapStitchingMethod = CubemapStitchingMethod.ROW
                             ) -> Union[List[Image.Image], Image.Image]:
    zoom = max(0, min(zoom, pano.max_zoom))
    faces = _generate_tile_list(pano.id, zoom)
    await _download_tiles_async(faces, session)
    return _stitch_panorama(faces, stitching_method=stitching_method)


def _parse_panos(response):
    panos = []
    for pano in response[1:]:  # first object is elapsed time
        pano_obj = _parse_pano(pano)
        panos.append(pano_obj)
    return panos


def _parse_pano(pano):
    # TODO: parse bl, nbn, pbn, ad fields
    # as it turns out, months/days without leading zeros
    # don't have a cross-platform format code in strptime.
    # wanna guess what kind of dates bing returns?
    datestr = pano["cd"]
    datestr = datestr.split("/")
    datestr[0] = datestr[0].rjust(2, "0")
    datestr[1] = datestr[1].rjust(2, "0")
    datestr = "/".join(datestr)
    date = datetime.strptime(datestr, "%m/%d/%Y %I:%M:%S %p")
    pano_obj = StreetsidePanorama(
        id=pano["id"],
        lat=pano["la"],
        lon=pano["lo"],
        date=date,
        next=pano["ne"] if "ne" in pano else None,
        previous=pano["pr"] if "pr" in pano else None,
        elevation=pano["al"] if "al" in pano else None,
        heading=math.radians(pano["he"]) if "he" in pano else None,
        pitch=math.radians(pano["pi"]) if "pi" in pano else None,
        roll=math.radians(pano["ro"]) if "ro" in pano else None,
        max_zoom=int(pano["ml"])
    )
    return pano_obj


def _generate_tile_list(panoid, zoom):
    """
    Generates a list of a panorama's tiles.
    Returns a list of faces and its tiles.
    """
    if zoom > 4:
        raise ValueError("Zoom can't be greater than 4")
    panoid_base4 = to_base4(panoid).rjust(16, "0")
    subdivs = pow(4, zoom)
    faces = {}
    for face_id in range(0, 6):
        face_id_base4 = to_base4(face_id + 1).rjust(2, "0")
        face_tiles = []
        for subdiv in range(0, subdivs):
            if zoom < 1:
                subdiv_base4 = ""
            else:
                subdiv_base4 = to_base4(subdiv).rjust(zoom, "0")
            tile_key = f"{face_id_base4}{subdiv_base4}"
            url = f"https://t.ssl.ak.tiles.virtualearth.net/tiles/hs{panoid_base4}{tile_key}.jpg?" \
                  f"g=13716"
            face_tiles.append({"face": face_id_base4, "subdiv": subdiv, "url": url})
        faces[face_id] = face_tiles
    return faces


def _download_tiles(faces):
    """
    Downloads the tiles of a panorama.
    """
    for face_id, face in faces.items():
        tiles = asyncio.run(download_files_async([tile["url"] for tile in face]))
        for idx, tile in enumerate(face):
            tile["image"] = tiles[idx]


async def _download_tiles_async(faces, session: ClientSession):
    """
    Downloads the tiles of a panorama.
    """
    for face_id, face in faces.items():
        tiles = await download_files_async([tile["url"] for tile in face], session)
        for idx, tile in enumerate(face):
            tile["image"] = tiles[idx]


def _stitch_four(face):
    """
    Stitches four consecutive individual tiles.
    """
    sub_tile = Image.new('RGB', (TILE_SIZE * 2, TILE_SIZE * 2))
    for idx, tile in enumerate(face[0:4]):
        tile_img = Image.open(BytesIO(tile["image"]))
        x = idx % 2
        y = idx // 2
        sub_tile.paste(im=tile_img, box=(x * TILE_SIZE, y * TILE_SIZE))
    return sub_tile


def _split_list(list_, size):
    return [list_[i:i + size] for i in range(0, len(list_), size)]


def _stitch_face(face):
    """
    Stitches one face of a panorama.
    """
    if len(face) <= 4:
        return _stitch_four(face)
    else:
        grid_size = int(math.sqrt(len(face)))
        stitched_tile_size = (grid_size // 2) * TILE_SIZE
        tile = Image.new('RGB', (stitched_tile_size * 2, stitched_tile_size * 2))
        split = _split_list(face, len(face) // 4)
        tile.paste(im=_stitch_face(split[0]), box=(0, 0))
        tile.paste(im=_stitch_face(split[1]), box=(stitched_tile_size, 0))
        tile.paste(im=_stitch_face(split[2]), box=(0, stitched_tile_size))
        tile.paste(im=_stitch_face(split[3]), box=(stitched_tile_size, stitched_tile_size))
        return tile


def _stitch_panorama(faces, stitching_method: CubemapStitchingMethod) -> Union[List[Image.Image], Image.Image]:
    """
    Stitches downloaded tiles into full faces or one full image.
    """
    full_tile_size = int(math.sqrt(len(faces[1]))) * TILE_SIZE

    stitched_faces = []
    if len(faces[1]) == 1:
        for i in range(0, 6):
            stitched_faces.append(Image.open(BytesIO(faces[i][0]["image"])))
    else:
        for i in range(0, 6):
            stitched_faces.append(_stitch_face(faces[i]))

    return stitch_cubemap_faces(stitched_faces, full_tile_size, stitching_method=stitching_method)
