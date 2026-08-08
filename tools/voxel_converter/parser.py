from __future__ import annotations

import json
import math
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MAPGRID_METATILE_ID_MASK = 0x03FF
MAPGRID_COLLISION_MASK = 0x0C00
MAPGRID_ELEVATION_MASK = 0xF000
MAPGRID_COLLISION_SHIFT = 10
MAPGRID_ELEVATION_SHIFT = 12

METATILE_ATTR_BEHAVIOR_MASK = 0x00FF
METATILE_ATTR_LAYER_MASK = 0xF000
METATILE_ATTR_LAYER_SHIFT = 12

NUM_TILES_IN_PRIMARY = 512
NUM_PALS_IN_PRIMARY = 6
WORDS_PER_METATILE = 8
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

Color = tuple[int, int, int, int]

_TILESET_BLOCK_RE = re.compile(
    r"const struct Tileset (?P<symbol>\w+)\s*=\s*\{(?P<body>.*?)\n\};",
    re.S,
)
_PALETTE_BLOCK_RE = re.compile(
    r"const u16 (?P<symbol>\w+)\[\]\[16\]\s*=\s*\{(?P<body>.*?)\n\};",
    re.S,
)
_INCGFX_PATH_RE = re.compile(r'INCGFX_U(?:16|32)\("(?P<path>[^"]+)"')
_INCBIN_PATH_RE = re.compile(
    r'const u(?:16|32) (?P<symbol>\w+)\[\]\s*=\s*INCBIN_U(?:16|32)\("(?P<path>[^"]+)"'
)
_TILES_PATH_RE = re.compile(
    r'const u32 (?P<symbol>\w+)\[\]\s*=\s*INCGFX_U32\("(?P<path>[^"]+)"'
)


@dataclass(frozen=True)
class TilesetSource:
    symbol: str
    is_secondary: bool
    tiles_png: Path
    palette_paths: tuple[Path, ...]
    metatiles_bin: Path
    metatile_attributes_bin: Path


@dataclass(frozen=True)
class TilesetData:
    source: TilesetSource
    atlas_width: int
    atlas_height: int
    atlas_pixels: tuple[int, ...]
    palettes: tuple[tuple[Color, ...], ...]
    metatiles: tuple[tuple[int, ...], ...]
    attributes: tuple[int, ...]

    @property
    def tile_count(self) -> int:
        return (self.atlas_width // 8) * (self.atlas_height // 8)

    def tile_pixel(self, tile_index: int, x: int, y: int) -> int | None:
        if tile_index < 0 or tile_index >= self.tile_count:
            return None
        tiles_per_row = self.atlas_width // 8
        tile_x = (tile_index % tiles_per_row) * 8 + x
        tile_y = (tile_index // tiles_per_row) * 8 + y
        return self.atlas_pixels[tile_y * self.atlas_width + tile_x]


@dataclass(frozen=True)
class ParsedCell:
    x: int
    y: int
    metatile_id: int
    collision: int
    elevation: int
    behavior: int
    behavior_name: str
    layer_type: int
    quadrant_colors: tuple[Color, Color, Color, Color]
    block_colors: tuple[Color, ...]
    block_coverage: tuple[int, ...]
    block_top_rows: tuple[int, ...]
    row_coverage: tuple[int, ...]


@dataclass(frozen=True)
class ParsedMap:
    map_id: str
    map_name: str
    layout_id: str
    width: int
    height: int
    primary_tileset: str
    secondary_tileset: str
    cells: tuple[ParsedCell, ...]


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for path in (current, *current.parents):
        if (path / "data/layouts/layouts.json").exists():
            return path
    raise FileNotFoundError("Could not locate repository root from tools/voxel_converter")


def parse_behavior_definitions(header_path: Path) -> dict[str, int]:
    lines = header_path.read_text(encoding="utf-8").splitlines()
    in_enum = False
    value = 0
    result: dict[str, int] = {}
    for raw_line in lines:
        line = raw_line.split("//", 1)[0].strip()
        if line == "enum {":
            in_enum = True
            continue
        if not in_enum:
            continue
        if line == "};":
            break
        if not line:
            continue
        entry = line.rstrip(",")
        if "=" in entry:
            name, assigned = (part.strip() for part in entry.split("=", 1))
            value = int(assigned, 0)
        else:
            name = entry
        result[name] = value
        value += 1
    return result


def load_behavior_material_names(mapping_path: Path) -> dict[str, str]:
    return json.loads(mapping_path.read_text(encoding="utf-8"))


def parse_tileset_sources(repo_root: Path) -> dict[str, TilesetSource]:
    headers_text = (repo_root / "src/data/tilesets/headers.h").read_text(encoding="utf-8")
    graphics_text = (
        (repo_root / "src/graphics.c").read_text(encoding="utf-8")
        + "\n"
        + (repo_root / "src/data/tilesets/graphics.h").read_text(encoding="utf-8")
    )
    metatiles_text = (repo_root / "src/data/tilesets/metatiles.h").read_text(encoding="utf-8")

    tile_paths = {
        match.group("symbol"): repo_root / match.group("path")
        for match in _TILES_PATH_RE.finditer(graphics_text)
    }
    data_paths = {
        match.group("symbol"): repo_root / match.group("path")
        for match in _INCBIN_PATH_RE.finditer(metatiles_text)
    }
    palette_paths: dict[str, tuple[Path, ...]] = {}
    for match in _PALETTE_BLOCK_RE.finditer(graphics_text):
        palette_paths[match.group("symbol")] = tuple(
            repo_root / path for path in _INCGFX_PATH_RE.findall(match.group("body"))
        )

    sources: dict[str, TilesetSource] = {}
    for match in _TILESET_BLOCK_RE.finditer(headers_text):
        body = match.group("body")
        symbol = match.group("symbol")
        is_secondary = _block_field(body, "isSecondary") == "TRUE"
        tiles_symbol = _block_field(body, "tiles")
        palettes_symbol = _block_field(body, "palettes")
        metatiles_symbol = _block_field(body, "metatiles")
        attributes_symbol = _block_field(body, "metatileAttributes")
        if not all((tiles_symbol, palettes_symbol, metatiles_symbol, attributes_symbol)):
            continue
        sources[symbol] = TilesetSource(
            symbol=symbol,
            is_secondary=is_secondary,
            tiles_png=tile_paths[tiles_symbol],
            palette_paths=palette_paths[palettes_symbol],
            metatiles_bin=data_paths[metatiles_symbol],
            metatile_attributes_bin=data_paths[attributes_symbol],
        )
    return sources


def load_tileset_data(source: TilesetSource) -> TilesetData:
    atlas_width, atlas_height, atlas_pixels = decode_indexed_png(source.tiles_png)
    palettes = tuple(tuple(parse_jasc_palette(path)) for path in source.palette_paths)
    metatile_words = read_u16_file(source.metatiles_bin)
    attributes = read_u16_file(source.metatile_attributes_bin)
    if len(metatile_words) != len(attributes) * WORDS_PER_METATILE:
        raise ValueError(
            f"Unexpected metatile word count for {source.symbol}: "
            f"{len(metatile_words)} words for {len(attributes)} attributes"
        )
    metatiles = tuple(
        tuple(metatile_words[index : index + WORDS_PER_METATILE])
        for index in range(0, len(metatile_words), WORDS_PER_METATILE)
    )
    return TilesetData(
        source=source,
        atlas_width=atlas_width,
        atlas_height=atlas_height,
        atlas_pixels=tuple(atlas_pixels),
        palettes=palettes,
        metatiles=metatiles,
        attributes=tuple(attributes),
    )


def parse_map(
    repo_root: Path,
    map_name: str,
    behavior_names: dict[int, str] | None = None,
) -> ParsedMap:
    metadata, map_dir_name = resolve_map_metadata(repo_root, map_name)
    layouts = json.loads((repo_root / "data/layouts/layouts.json").read_text(encoding="utf-8"))["layouts"]
    layout = next(item for item in layouts if item["id"] == metadata["layout"])

    tileset_sources = parse_tileset_sources(repo_root)
    primary = load_tileset_data(tileset_sources[layout["primary_tileset"]])
    secondary = load_tileset_data(tileset_sources[layout["secondary_tileset"]])
    if behavior_names is None:
        behavior_values = parse_behavior_definitions(
            repo_root / "include/constants/metatile_behaviors.h"
        )
        behavior_names = {value: name for name, value in behavior_values.items()}

    block_path = repo_root / layout["blockdata_filepath"]
    blocks = read_u16_file(block_path)
    expected_blocks = layout["width"] * layout["height"]
    if len(blocks) != expected_blocks:
        raise ValueError(
            f"{block_path} has {len(blocks)} blocks, expected {expected_blocks}"
        )

    cells: list[ParsedCell] = []
    for index, block in enumerate(blocks):
        x = index % layout["width"]
        y = index // layout["width"]
        metatile_id = block & MAPGRID_METATILE_ID_MASK
        collision = (block & MAPGRID_COLLISION_MASK) >> MAPGRID_COLLISION_SHIFT
        elevation = (block & MAPGRID_ELEVATION_MASK) >> MAPGRID_ELEVATION_SHIFT
        attributes = metatile_attributes(primary, secondary, metatile_id)
        behavior = attributes & METATILE_ATTR_BEHAVIOR_MASK
        layer_type = (attributes & METATILE_ATTR_LAYER_MASK) >> METATILE_ATTR_LAYER_SHIFT
        quadrant_colors, block_colors, block_coverage, block_top_rows, row_coverage = (
            composite_cell_metrics(primary, secondary, metatile_id)
        )
        cells.append(
            ParsedCell(
                x=x,
                y=y,
                metatile_id=metatile_id,
                collision=collision,
                elevation=elevation,
                behavior=behavior,
                behavior_name=behavior_names.get(behavior, f"MB_UNKNOWN_{behavior:02X}"),
                layer_type=layer_type,
                quadrant_colors=quadrant_colors,
                block_colors=block_colors,
                block_coverage=block_coverage,
                block_top_rows=block_top_rows,
                row_coverage=row_coverage,
            )
        )

    return ParsedMap(
        map_id=metadata["id"],
        map_name=map_dir_name,
        layout_id=layout["id"],
        width=layout["width"],
        height=layout["height"],
        primary_tileset=layout["primary_tileset"],
        secondary_tileset=layout["secondary_tileset"],
        cells=tuple(cells),
    )


def resolve_map_metadata(repo_root: Path, map_name: str) -> tuple[dict[str, object], str]:
    direct = repo_root / "data/maps" / map_name / "map.json"
    if direct.exists():
        return json.loads(direct.read_text(encoding="utf-8")), map_name

    needle = map_name.lower()
    for path in (repo_root / "data/maps").glob("*/map.json"):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        candidates = (
            path.parent.name.lower(),
            str(metadata.get("name", "")).lower(),
            str(metadata.get("id", "")).lower(),
            str(metadata.get("layout", "")).lower(),
        )
        if needle in candidates:
            return metadata, path.parent.name
    raise FileNotFoundError(f"Could not find map metadata for {map_name}")


def metatile_attributes(primary: TilesetData, secondary: TilesetData, metatile_id: int) -> int:
    if metatile_id < NUM_TILES_IN_PRIMARY:
        if metatile_id >= len(primary.attributes):
            return 0
        return primary.attributes[metatile_id]
    local_id = metatile_id - NUM_TILES_IN_PRIMARY
    if local_id >= len(secondary.attributes):
        return 0
    return secondary.attributes[local_id]


def composite_quadrant_colors(
    primary: TilesetData,
    secondary: TilesetData,
    metatile_id: int,
) -> tuple[Color, Color, Color, Color]:
    owner, local_id = metatile_owner(primary, secondary, metatile_id)
    if local_id >= len(owner.metatiles):
        return ((0, 0, 0, 0),) * 4

    entries = owner.metatiles[local_id]
    quadrant_colors: list[Color] = []
    for quadrant in range(4):
        pixels: list[Color] = []
        for py in range(8):
            for px in range(8):
                color = sample_tile_entry(primary, secondary, entries[4 + quadrant], px, py)
                if color is None:
                    color = sample_tile_entry(primary, secondary, entries[quadrant], px, py)
                if color is not None:
                    pixels.append(color)
        quadrant_colors.append(average_color(pixels))
    return tuple(quadrant_colors)  # type: ignore[return-value]


def composite_block_colors(
    primary: TilesetData,
    secondary: TilesetData,
    metatile_id: int,
) -> tuple[Color, ...]:
    owner, local_id = metatile_owner(primary, secondary, metatile_id)
    if local_id >= len(owner.metatiles):
        return ((0, 0, 0, 0),) * 16

    entries = owner.metatiles[local_id]
    block_colors: list[Color] = []
    for block_y in range(4):
        for block_x in range(4):
            pixels: list[Color] = []
            start_x = block_x * 4
            start_y = block_y * 4
            for py in range(start_y, start_y + 4):
                for px in range(start_x, start_x + 4):
                    color = composite_metatile_pixel(primary, secondary, entries, px, py)
                    if color is not None:
                        pixels.append(color)
            block_colors.append(average_color(pixels))
    return tuple(block_colors)


def composite_cell_metrics(
    primary: TilesetData,
    secondary: TilesetData,
    metatile_id: int,
) -> tuple[
    tuple[Color, Color, Color, Color],
    tuple[Color, ...],
    tuple[int, ...],
    tuple[int, ...],
    tuple[int, ...],
]:
    owner, local_id = metatile_owner(primary, secondary, metatile_id)
    if local_id >= len(owner.metatiles):
        empty_colors = ((0, 0, 0, 0),) * 4
        empty_blocks = ((0, 0, 0, 0),) * 16
        empty_metrics = (0,) * 16
        empty_top_rows = (16,) * 16
        empty_rows = (0,) * 16
        return empty_colors, empty_blocks, empty_metrics, empty_top_rows, empty_rows

    entries = owner.metatiles[local_id]
    pixels: list[Color] = []
    for y in range(16):
        for x in range(16):
            pixels.append(composite_metatile_pixel(primary, secondary, entries, x, y) or (0, 0, 0, 0))

    quadrant_colors: list[Color] = []
    for quadrant in range(4):
        start_x = (quadrant % 2) * 8
        start_y = (quadrant // 2) * 8
        samples = [
            pixels[(start_y + py) * 16 + start_x + px]
            for py in range(8)
            for px in range(8)
        ]
        quadrant_colors.append(average_color(samples))

    block_colors: list[Color] = []
    block_coverage: list[int] = []
    block_top_rows: list[int] = []
    for block_y in range(4):
        for block_x in range(4):
            start_x = block_x * 4
            start_y = block_y * 4
            samples = [
                pixels[(start_y + py) * 16 + start_x + px]
                for py in range(4)
                for px in range(4)
            ]
            block_colors.append(average_color(samples))
            opaque_rows = [
                start_y + py
                for py in range(4)
                if any(samples[py * 4 + px][3] for px in range(4))
            ]
            block_coverage.append(sum(1 for color in samples if color[3]))
            block_top_rows.append(min(opaque_rows) if opaque_rows else 16)

    row_coverage = tuple(
        sum(1 for x in range(16) if pixels[y * 16 + x][3])
        for y in range(16)
    )
    return (
        tuple(quadrant_colors),  # type: ignore[return-value]
        tuple(block_colors),
        tuple(block_coverage),
        tuple(block_top_rows),
        row_coverage,
    )


def metatile_owner(
    primary: TilesetData,
    secondary: TilesetData,
    metatile_id: int,
) -> tuple[TilesetData, int]:
    if metatile_id < NUM_TILES_IN_PRIMARY:
        return primary, metatile_id
    return secondary, metatile_id - NUM_TILES_IN_PRIMARY


def composite_metatile_pixel(
    primary: TilesetData,
    secondary: TilesetData,
    entries: tuple[int, ...],
    x: int,
    y: int,
) -> Color | None:
    quadrant = (y // 8) * 2 + (x // 8)
    local_x = x % 8
    local_y = y % 8
    color = sample_tile_entry(primary, secondary, entries[4 + quadrant], local_x, local_y)
    if color is None:
        color = sample_tile_entry(primary, secondary, entries[quadrant], local_x, local_y)
    return color


def sample_tile_entry(
    primary: TilesetData,
    secondary: TilesetData,
    entry: int,
    x: int,
    y: int,
) -> Color | None:
    tile_id = entry & 0x3FF
    if tile_id < NUM_TILES_IN_PRIMARY:
        tile_source = primary
        local_tile = tile_id
    else:
        tile_source = secondary
        local_tile = tile_id - NUM_TILES_IN_PRIMARY

    px = 7 - x if entry & 0x400 else x
    py = 7 - y if entry & 0x800 else y
    palette_bank = (entry >> 12) & 0xF
    palette_source = primary if palette_bank < NUM_PALS_IN_PRIMARY else secondary
    pixel_index = tile_source.tile_pixel(local_tile, px, py)
    if pixel_index is None or pixel_index == 0:
        return None
    if palette_bank >= len(palette_source.palettes):
        return None
    palette = palette_source.palettes[palette_bank]
    if pixel_index >= len(palette):
        return None
    return palette[pixel_index]


def average_color(colors: Iterable[Color]) -> Color:
    colors = [color for color in colors if color[3]]
    if not colors:
        return (0, 0, 0, 0)
    r = sum(color[0] for color in colors) // len(colors)
    g = sum(color[1] for color in colors) // len(colors)
    b = sum(color[2] for color in colors) // len(colors)
    a = sum(color[3] for color in colors) // len(colors)
    return (r, g, b, a)


def parse_jasc_palette(path: Path) -> list[Color]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 3 or lines[0] != "JASC-PAL":
        raise ValueError(f"{path} is not a JASC-PAL palette")
    count = int(lines[2])
    colors: list[Color] = []
    for line in lines[3 : 3 + count]:
        r, g, b = (int(part) for part in line.split())
        colors.append((r, g, b, 255))
    return colors


def read_u16_file(path: Path) -> list[int]:
    data = path.read_bytes()
    if len(data) % 2:
        raise ValueError(f"{path} length is not divisible by 2")
    return list(struct.unpack(f"<{len(data) // 2}H", data))


def decode_indexed_png(path: Path) -> tuple[int, int, list[int]]:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError(f"{path} is not a PNG file")

    width = height = bit_depth = color_type = interlace = None
    compressed = bytearray()
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        chunk_length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + chunk_length]
        offset += 12 + chunk_length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None or bit_depth is None or color_type is None:
        raise ValueError(f"{path} is missing IHDR data")
    if color_type != 3 or bit_depth not in (4, 8) or interlace != 0:
        raise ValueError(
            f"{path} must be a non-interlaced indexed PNG with bit depth 4 or 8"
        )

    row_bytes = math.ceil(width * bit_depth / 8)
    raw = zlib.decompress(bytes(compressed))
    expected = (row_bytes + 1) * height
    if len(raw) != expected:
        raise ValueError(f"{path} decoded to {len(raw)} bytes, expected {expected}")

    pixels: list[int] = []
    bpp = 1
    previous = bytes(row_bytes)
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        filtered = bytearray(raw[cursor : cursor + row_bytes])
        cursor += row_bytes
        unfiltered = _unfilter_scanline(filter_type, filtered, previous, bpp)
        previous = bytes(unfiltered)
        if bit_depth == 8:
            pixels.extend(unfiltered)
        else:
            for byte in unfiltered:
                pixels.append(byte >> 4)
                if width % 2 == 1 and len(pixels) % width == 0:
                    continue
                pixels.append(byte & 0x0F)
    return width, height, pixels[: width * height]


def _unfilter_scanline(filter_type: int, scanline: bytearray, previous: bytes, bpp: int) -> bytearray:
    if filter_type == 0:
        return scanline
    if filter_type == 1:
        for index in range(len(scanline)):
            left = scanline[index - bpp] if index >= bpp else 0
            scanline[index] = (scanline[index] + left) & 0xFF
        return scanline
    if filter_type == 2:
        for index in range(len(scanline)):
            scanline[index] = (scanline[index] + previous[index]) & 0xFF
        return scanline
    if filter_type == 3:
        for index in range(len(scanline)):
            left = scanline[index - bpp] if index >= bpp else 0
            up = previous[index]
            scanline[index] = (scanline[index] + ((left + up) // 2)) & 0xFF
        return scanline
    if filter_type == 4:
        for index in range(len(scanline)):
            left = scanline[index - bpp] if index >= bpp else 0
            up = previous[index]
            up_left = previous[index - bpp] if index >= bpp else 0
            scanline[index] = (scanline[index] + _paeth(left, up, up_left)) & 0xFF
        return scanline
    raise ValueError(f"Unsupported PNG filter type {filter_type}")


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _block_field(body: str, field_name: str) -> str:
    match = re.search(rf"\.{field_name}\s*=\s*(\w+)", body)
    return match.group(1) if match else ""
