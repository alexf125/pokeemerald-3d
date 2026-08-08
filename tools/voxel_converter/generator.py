from __future__ import annotations

from dataclasses import dataclass

from .parser import Color, ParsedCell, ParsedMap

DEFAULT_BEHAVIOR_MATERIAL = "ground"
ELEVATION_TRANSITION = 0
ELEVATION_MULTI_LEVEL = 15
SUBVOXEL_SCALE = 4
STRUCTURE_HEIGHT = 4
RAMP_BEHAVIOR_TOKENS = ("STAIR", "LADDER", "ESCALATOR")
STRUCTURE_BEHAVIOR_TOKENS = ("DOOR", "PC", "COUNTER", "TELEVISION", "REGION_MAP")


@dataclass(frozen=True)
class MaterialSpec:
    name: str
    fallback_color: Color
    alpha: int = 255
    height_bonus: int = 0


MATERIAL_SPECS: dict[str, MaterialSpec] = {
    "ground": MaterialSpec("ground", (110, 154, 90, 255)),
    "grass": MaterialSpec("grass", (74, 142, 66, 255), height_bonus=1),
    "tall_grass": MaterialSpec("tall_grass", (58, 128, 54, 255), height_bonus=2),
    "water": MaterialSpec("water", (62, 120, 198, 255), alpha=160),
    "sand": MaterialSpec("sand", (214, 192, 121, 255)),
    "rock": MaterialSpec("rock", (118, 118, 124, 255)),
    "ice": MaterialSpec("ice", (170, 216, 240, 255), alpha=220),
    "structure": MaterialSpec("structure", (156, 132, 106, 255)),
}


@dataclass(frozen=True)
class Voxel:
    x: int
    y: int
    z: int
    color: Color
    material: str


@dataclass(frozen=True)
class VoxelModel:
    map_name: str
    size_x: int
    size_y: int
    size_z: int
    voxels: tuple[Voxel, ...]
    materials: dict[str, MaterialSpec]
    metadata: dict[str, object]


def generate_voxel_model(
    parsed_map: ParsedMap,
    behavior_materials: dict[str, str],
    elevation_scale: int = 2,
) -> VoxelModel:
    voxels: list[Voxel] = []
    max_height = 0
    used_materials: dict[str, MaterialSpec] = {}
    materials_by_cell = {
        (cell.x, cell.y): resolve_material(
            cell.behavior_name, cell.layer_type, cell.collision, behavior_materials
        )
        for cell in parsed_map.cells
    }
    cells_by_pos = {(cell.x, cell.y): cell for cell in parsed_map.cells}
    resolved_elevations = resolve_elevations(parsed_map, materials_by_cell, cells_by_pos)

    for cell in parsed_map.cells:
        material_name = materials_by_cell[(cell.x, cell.y)]
        spec = MATERIAL_SPECS.get(material_name, MATERIAL_SPECS[DEFAULT_BEHAVIOR_MATERIAL])
        used_materials[spec.name] = spec
        block_heights = build_block_heights(
            cell,
            parsed_map,
            cells_by_pos,
            materials_by_cell,
            resolved_elevations,
            elevation_scale,
            spec,
        )
        max_height = max(max_height, max(block_heights, default=0))
        for block_index, color in enumerate(cell.block_colors):
            world_x = cell.x * SUBVOXEL_SCALE + (block_index % SUBVOXEL_SCALE)
            world_y = cell.y * SUBVOXEL_SCALE + (block_index // SUBVOXEL_SCALE)
            voxel_color = material_color(spec, color)
            for z in range(block_heights[block_index] + 1):
                voxels.append(
                    Voxel(
                        x=world_x,
                        y=world_y,
                        z=z,
                        color=voxel_color,
                        material=spec.name,
                    )
                )

    metadata = {
        "layout_id": parsed_map.layout_id,
        "primary_tileset": parsed_map.primary_tileset,
        "secondary_tileset": parsed_map.secondary_tileset,
        "elevation_scale": elevation_scale,
        "subvoxel_scale": SUBVOXEL_SCALE,
        "cell_width": parsed_map.width,
        "cell_height": parsed_map.height,
    }
    return VoxelModel(
        map_name=parsed_map.map_name,
        size_x=parsed_map.width * SUBVOXEL_SCALE,
        size_y=parsed_map.height * SUBVOXEL_SCALE,
        size_z=max_height + 1,
        voxels=tuple(voxels),
        materials=used_materials,
        metadata=metadata,
    )


def resolve_material(
    behavior_name: str,
    layer_type: int,
    collision: int,
    behavior_materials: dict[str, str],
) -> str:
    if behavior_name in behavior_materials:
        return behavior_materials[behavior_name]
    if any(token in behavior_name for token in RAMP_BEHAVIOR_TOKENS):
        return DEFAULT_BEHAVIOR_MATERIAL
    if layer_type != 0 or collision:
        return "structure"
    return DEFAULT_BEHAVIOR_MATERIAL


def material_color(spec: MaterialSpec, source_color: Color) -> Color:
    if source_color[3]:
        return (source_color[0], source_color[1], source_color[2], spec.alpha)
    fallback = spec.fallback_color
    return (fallback[0], fallback[1], fallback[2], spec.alpha)


def resolve_elevations(
    parsed_map: ParsedMap,
    materials_by_cell: dict[tuple[int, int], str],
    cells_by_pos: dict[tuple[int, int], ParsedCell],
) -> dict[tuple[int, int], int]:
    resolved: dict[tuple[int, int], int] = {}
    for cell in parsed_map.cells:
        if cell.elevation not in (ELEVATION_TRANSITION, ELEVATION_MULTI_LEVEL):
            resolved[(cell.x, cell.y)] = cell.elevation

    # Three passes are enough for the local transition patterns used in these
    # maps because stairs, ladders, doors, and bridge joins only need to inherit
    # nearby elevations from immediately adjacent tiles.
    for _ in range(3):
        changed = False
        for cell in parsed_map.cells:
            position = (cell.x, cell.y)
            if position in resolved:
                continue
            neighbor_levels = neighboring_levels(cell.x, cell.y, cells_by_pos, resolved)
            if cell.elevation == ELEVATION_MULTI_LEVEL:
                level = dominant_level(neighbor_levels) or inherited_level(cell, materials_by_cell, neighbor_levels)
            else:
                level = inherited_level(cell, materials_by_cell, neighbor_levels)
            if level is None:
                continue
            resolved[position] = level
            changed = True
        if not changed:
            break

    for cell in parsed_map.cells:
        position = (cell.x, cell.y)
        if position not in resolved:
            resolved[position] = inherited_level(
                cell,
                materials_by_cell,
                neighboring_levels(cell.x, cell.y, cells_by_pos, resolved),
            ) or 0
    return resolved


def build_block_heights(
    cell: ParsedCell,
    parsed_map: ParsedMap,
    cells_by_pos: dict[tuple[int, int], ParsedCell],
    materials_by_cell: dict[tuple[int, int], str],
    resolved_elevations: dict[tuple[int, int], int],
    elevation_scale: int,
    spec: MaterialSpec,
) -> tuple[int, ...]:
    material_name = materials_by_cell[(cell.x, cell.y)]
    base_level = resolved_elevations[(cell.x, cell.y)]
    top_offset = spec.height_bonus + (STRUCTURE_HEIGHT if material_name == "structure" else 0)
    flat_height = base_level * elevation_scale + top_offset
    neighbor_levels = directional_levels(cell.x, cell.y, cells_by_pos, resolved_elevations)
    if should_ramp(cell, material_name, neighbor_levels):
        return ramp_block_heights(base_level, neighbor_levels, elevation_scale, top_offset)
    return tuple(flat_height for _ in range(SUBVOXEL_SCALE * SUBVOXEL_SCALE))


def neighboring_levels(
    x: int,
    y: int,
    cells_by_pos: dict[tuple[int, int], ParsedCell],
    resolved_elevations: dict[tuple[int, int], int],
) -> list[int]:
    levels: list[int] = []
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        neighbor = cells_by_pos.get((x + dx, y + dy))
        if neighbor is None:
            continue
        level = resolved_elevations.get((x + dx, y + dy))
        if level is not None:
            levels.append(level)
        elif neighbor.elevation not in (ELEVATION_TRANSITION, ELEVATION_MULTI_LEVEL):
            levels.append(neighbor.elevation)
    return levels


def directional_levels(
    x: int,
    y: int,
    cells_by_pos: dict[tuple[int, int], ParsedCell],
    resolved_elevations: dict[tuple[int, int], int],
) -> dict[str, int]:
    levels: dict[str, int] = {}
    for name, dx, dy in (("north", 0, -1), ("east", 1, 0), ("south", 0, 1), ("west", -1, 0)):
        level = resolved_elevations.get((x + dx, y + dy))
        if level is None:
            neighbor = cells_by_pos.get((x + dx, y + dy))
            if neighbor is not None and neighbor.elevation not in (ELEVATION_TRANSITION, ELEVATION_MULTI_LEVEL):
                level = neighbor.elevation
        if level is not None:
            levels[name] = level
    return levels


def inherited_level(
    cell: ParsedCell,
    materials_by_cell: dict[tuple[int, int], str],
    neighbor_levels: list[int],
) -> int | None:
    if not neighbor_levels:
        return None
    material_name = materials_by_cell[(cell.x, cell.y)]
    if cell.elevation == ELEVATION_MULTI_LEVEL or material_name == "structure" or cell.collision or cell.layer_type != 0:
        positive_levels = [level for level in neighbor_levels if level > 0]
        if positive_levels:
            return dominant_level(positive_levels) or max(positive_levels)
    if cell.elevation == ELEVATION_TRANSITION:
        positive_levels = [level for level in neighbor_levels if level > 0]
        if positive_levels:
            return min(positive_levels)
    return None


def dominant_level(levels: list[int]) -> int | None:
    if not levels:
        return None
    counts: dict[int, int] = {}
    for level in levels:
        counts[level] = counts.get(level, 0) + 1
    return max(counts, key=lambda level: (counts[level], level))


def should_ramp(
    cell: ParsedCell,
    material_name: str,
    neighbor_levels: dict[str, int],
) -> bool:
    if material_name in ("water", "structure"):
        return False
    values = list(neighbor_levels.values())
    if not values or min(values) == max(values):
        return False
    if any(token in cell.behavior_name for token in RAMP_BEHAVIOR_TOKENS):
        return True
    return cell.elevation == ELEVATION_TRANSITION and not (
        cell.collision or cell.layer_type != 0 or any(token in cell.behavior_name for token in STRUCTURE_BEHAVIOR_TOKENS)
    )


def ramp_block_heights(
    base_level: int,
    neighbor_levels: dict[str, int],
    elevation_scale: int,
    top_offset: int,
) -> tuple[int, ...]:
    west = neighbor_levels.get("west", base_level)
    east = neighbor_levels.get("east", base_level)
    north = neighbor_levels.get("north", base_level)
    south = neighbor_levels.get("south", base_level)
    delta_x = east - west
    delta_y = south - north
    heights: list[int] = []
    if abs(delta_x) >= abs(delta_y):
        start = west
        end = east
        for block_y in range(SUBVOXEL_SCALE):
            for block_x in range(SUBVOXEL_SCALE):
                t = (block_x + 0.5) / SUBVOXEL_SCALE
                level = round(start + (end - start) * t)
                heights.append(level * elevation_scale + top_offset)
    else:
        start = north
        end = south
        for block_y in range(SUBVOXEL_SCALE):
            t = (block_y + 0.5) / SUBVOXEL_SCALE
            level = round(start + (end - start) * t)
            for _block_x in range(SUBVOXEL_SCALE):
                heights.append(level * elevation_scale + top_offset)
    return tuple(heights)
