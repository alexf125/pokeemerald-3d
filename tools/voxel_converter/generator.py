from __future__ import annotations

from dataclasses import dataclass

from .parser import Color, ParsedCell, ParsedMap

DEFAULT_BEHAVIOR_MATERIAL = "ground"
ELEVATION_TRANSITION = 0
ELEVATION_MULTI_LEVEL = 15
SUBVOXEL_SCALE = 4
MAX_BUILDING_BANDS = 6
RAMP_BEHAVIOR_TOKENS = ("STAIR", "LADDER", "ESCALATOR")
STRUCTURE_BEHAVIOR_TOKENS = (
    "DOOR",
    "PC",
    "COUNTER",
    "TELEVISION",
    "REGION_MAP",
    "POKEBLOCK",
    "ROULETTE",
    "SLOT_MACHINE",
)
OBJECT_BEHAVIOR_TOKENS = (
    "BERRY",
    "DECORATION",
    "SCENERY",
    "SIGN",
    "CABLE_BOX",
)
SHAPE_HEIGHTS = {
    "ground": 0,
    "water": -1,
    "ledge": 2,
    "fence": 3,
    "sign": 3,
    "wall": 4,
    "roof": 7,
    "tree": 4,
    "cliff": 8,
    "object": 3,
}
AUTHORED_POSITION_SHAPES: dict[tuple[str, int, int], "ShapeRecord"] = {}
AUTHORED_FLAT_SHAPES: dict[str, "ShapeRecord"] = {}


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
class ShapeRecord:
    shape_class: str
    height: int
    art_mode: str
    flat: bool
    authored: bool = False


@dataclass(frozen=True)
class BuildingProfile:
    front: tuple[int, int]
    span: tuple[int, int]
    direction: tuple[int, int]
    bands: tuple[ParsedCell, ...]


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
    used_materials: dict[str, MaterialSpec] = {}
    materials_by_cell = {
        (cell.x, cell.y): resolve_material(
            cell.behavior_name, cell.layer_type, cell.collision, behavior_materials
        )
        for cell in parsed_map.cells
    }
    cells_by_pos = {(cell.x, cell.y): cell for cell in parsed_map.cells}
    resolved_elevations = resolve_elevations(parsed_map, materials_by_cell, cells_by_pos)
    building_profiles, suppressed_positions = resolve_building_profiles(
        parsed_map, materials_by_cell, cells_by_pos
    )
    shapes = resolve_shapes(
        parsed_map,
        materials_by_cell,
        cells_by_pos,
        resolved_elevations,
        building_profiles,
        suppressed_positions,
    )

    min_height = 0
    max_height = 0
    for cell in parsed_map.cells:
        position = (cell.x, cell.y)
        if position in suppressed_positions:
            continue
        shape = shapes[position]
        if shape.shape_class == "void":
            continue
        material_name = materials_by_cell[position]
        spec = MATERIAL_SPECS.get(material_name, MATERIAL_SPECS[DEFAULT_BEHAVIOR_MATERIAL])
        used_materials[spec.name] = spec

        if shape.art_mode == "building_fold":
            cell_voxels = build_building_voxels(
                cell,
                building_profiles[position],
                resolved_elevations[position],
                spec,
                elevation_scale,
            )
        elif shape.art_mode == "object_pixels":
            cell_voxels = build_object_voxels(
                cell,
                resolved_elevations[position],
                shape,
                spec,
                elevation_scale,
            )
        else:
            block_heights = build_block_heights(
                cell,
                cells_by_pos,
                materials_by_cell,
                resolved_elevations,
                elevation_scale,
                spec,
                shape,
            )
            cell_voxels = build_column_voxels(cell, block_heights, spec)

        for voxel in cell_voxels:
            min_height = min(min_height, voxel.z)
            max_height = max(max_height, voxel.z)
            voxels.append(voxel)

    z_shift = -min_height if min_height < 0 else 0
    shifted_voxels = tuple(
        Voxel(voxel.x, voxel.y, voxel.z + z_shift, voxel.color, voxel.material) for voxel in voxels
    )
    metadata = {
        "layout_id": parsed_map.layout_id,
        "primary_tileset": parsed_map.primary_tileset,
        "secondary_tileset": parsed_map.secondary_tileset,
        "elevation_scale": elevation_scale,
        "subvoxel_scale": SUBVOXEL_SCALE,
        "cell_width": parsed_map.width,
        "cell_height": parsed_map.height,
        "z_offset": z_shift,
        "shape_counts": summarize_shapes(shapes),
    }
    return VoxelModel(
        map_name=parsed_map.map_name,
        size_x=parsed_map.width * SUBVOXEL_SCALE,
        size_y=parsed_map.height * SUBVOXEL_SCALE,
        size_z=max_height + z_shift + 1,
        voxels=shifted_voxels,
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

    for _ in range(3):
        changed = False
        for cell in parsed_map.cells:
            position = (cell.x, cell.y)
            if position in resolved:
                continue
            neighbor_levels = neighboring_levels(cell.x, cell.y, cells_by_pos, resolved)
            if cell.elevation == ELEVATION_MULTI_LEVEL:
                level = dominant_level(neighbor_levels)
                if level is None:
                    level = inherited_level(cell, materials_by_cell, neighbor_levels)
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


def resolve_building_profiles(
    parsed_map: ParsedMap,
    materials_by_cell: dict[tuple[int, int], str],
    cells_by_pos: dict[tuple[int, int], ParsedCell],
) -> tuple[dict[tuple[int, int], BuildingProfile], set[tuple[int, int]]]:
    profiles: dict[tuple[int, int], BuildingProfile] = {}
    suppressed: set[tuple[int, int]] = set()
    walkable_cache = {
        (cell.x, cell.y): is_walkable_cell(cell, materials_by_cell[(cell.x, cell.y)])
        for cell in parsed_map.cells
    }

    for cell in parsed_map.cells:
        position = (cell.x, cell.y)
        material_name = materials_by_cell[position]
        direction = detect_building_direction(cell, materials_by_cell, cells_by_pos)
        if not is_door_anchor(cell, material_name, direction):
            continue
        span = detect_front_span(cell, direction, materials_by_cell, cells_by_pos)
        positions = front_positions_for_span(cell, direction, span)
        for front_position in positions:
            if front_position in profiles:
                continue
            front_cell = cells_by_pos.get(front_position)
            if front_cell is None:
                continue
            bands = measure_building_bands(front_cell, direction, materials_by_cell, cells_by_pos)
            if len(bands) < 2:
                continue
            profiles[front_position] = BuildingProfile(
                front=front_position,
                span=span,
                direction=direction,
                bands=tuple(bands),
            )
            suppressed.update((band.x, band.y) for band in bands[1:])
    return profiles, suppressed


def resolve_shapes(
    parsed_map: ParsedMap,
    materials_by_cell: dict[tuple[int, int], str],
    cells_by_pos: dict[tuple[int, int], ParsedCell],
    resolved_elevations: dict[tuple[int, int], int],
    building_profiles: dict[tuple[int, int], BuildingProfile],
    suppressed_positions: set[tuple[int, int]],
) -> dict[tuple[int, int], ShapeRecord]:
    shapes: dict[tuple[int, int], ShapeRecord] = {}
    for cell in parsed_map.cells:
        position = (cell.x, cell.y)
        authored = AUTHORED_POSITION_SHAPES.get((parsed_map.map_name, cell.x, cell.y))
        if authored is not None:
            shapes[position] = authored
            continue
        authored = AUTHORED_FLAT_SHAPES.get(cell.behavior_name)
        if authored is not None:
            shapes[position] = authored
            continue
        if position in suppressed_positions:
            shapes[position] = ShapeRecord("void", -1, "hidden", True)
            continue
        if position in building_profiles:
            bands = len(building_profiles[position].bands)
            height = max(SHAPE_HEIGHTS["wall"], min(SHAPE_HEIGHTS["roof"], bands + 1))
            shapes[position] = ShapeRecord("building", height, "building_fold", False)
            continue

        material_name = materials_by_cell[position]
        neighbor_levels = directional_levels(cell.x, cell.y, cells_by_pos, resolved_elevations)
        if is_void_tile(cell):
            shapes[position] = ShapeRecord("void", -1, "void", True)
        elif material_name == "water":
            shapes[position] = ShapeRecord("water", SHAPE_HEIGHTS["water"], "flat", True)
        elif any(token in cell.behavior_name for token in RAMP_BEHAVIOR_TOKENS):
            shapes[position] = ShapeRecord("ledge", SHAPE_HEIGHTS["ledge"], "ramp", False)
        elif is_sparse_object_candidate(cell, material_name):
            shape_class = "tree" if material_name in ("grass", "tall_grass") else "object"
            shapes[position] = ShapeRecord(
                shape_class,
                measured_object_height(cell),
                "object_pixels",
                False,
            )
        elif material_name == "rock" and neighbor_cliff_height(resolved_elevations, cell, neighbor_levels) >= 2:
            shapes[position] = ShapeRecord("cliff", SHAPE_HEIGHTS["cliff"], "flat", True)
        elif material_name == "structure" or cell.layer_type != 0 or cell.collision:
            shapes[position] = ShapeRecord("wall", SHAPE_HEIGHTS["wall"], "flat", True)
        else:
            shapes[position] = ShapeRecord(material_name, 0, "flat", True)
    return shapes


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
    if prefers_raised_level(cell, material_name):
        positive_levels = [level for level in neighbor_levels if level > 0]
        if positive_levels:
            level = dominant_level(positive_levels)
            if level is not None:
                return level
            return max(positive_levels)
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


def build_block_heights(
    cell: ParsedCell,
    cells_by_pos: dict[tuple[int, int], ParsedCell],
    materials_by_cell: dict[tuple[int, int], str],
    resolved_elevations: dict[tuple[int, int], int],
    elevation_scale: int,
    spec: MaterialSpec,
    shape: ShapeRecord,
) -> tuple[int, ...]:
    material_name = materials_by_cell[(cell.x, cell.y)]
    base_level = resolved_elevations[(cell.x, cell.y)]
    base_height = base_level * elevation_scale
    top_offset = spec.height_bonus + shape.height
    if shape.shape_class == "water":
        top_offset = shape.height
    neighbor_levels = directional_levels(cell.x, cell.y, cells_by_pos, resolved_elevations)
    if should_ramp(cell, material_name, neighbor_levels):
        return ramp_block_heights(base_level, neighbor_levels, elevation_scale, max(spec.height_bonus, shape.height))
    flat_height = base_height + top_offset
    return tuple(flat_height for _ in range(SUBVOXEL_SCALE * SUBVOXEL_SCALE))


def build_column_voxels(
    cell: ParsedCell,
    block_heights: tuple[int, ...],
    spec: MaterialSpec,
) -> list[Voxel]:
    voxels: list[Voxel] = []
    for block_index, color in enumerate(cell.block_colors):
        if cell.block_coverage[block_index] == 0:
            continue
        world_x = cell.x * SUBVOXEL_SCALE + (block_index % SUBVOXEL_SCALE)
        world_y = cell.y * SUBVOXEL_SCALE + (block_index // SUBVOXEL_SCALE)
        voxel_color = material_color(spec, color)
        for z in inclusive_height_range(block_heights[block_index]):
            voxels.append(Voxel(world_x, world_y, z, voxel_color, spec.name))
    return voxels


def build_object_voxels(
    cell: ParsedCell,
    resolved_elevation: int,
    shape: ShapeRecord,
    spec: MaterialSpec,
    elevation_scale: int,
) -> list[Voxel]:
    voxels: list[Voxel] = []
    base_height = resolved_elevation * elevation_scale
    for block_index, color in enumerate(cell.block_colors):
        coverage = cell.block_coverage[block_index]
        if coverage == 0:
            continue
        world_x = cell.x * SUBVOXEL_SCALE + (block_index % SUBVOXEL_SCALE)
        world_y = cell.y * SUBVOXEL_SCALE + (block_index // SUBVOXEL_SCALE)
        top_row = cell.block_top_rows[block_index]
        local_height = max(1, min(shape.height, ((16 - top_row) + 3) // 4))
        voxel_color = material_color(spec, color)
        for z in range(base_height, base_height + local_height + 1):
            voxels.append(Voxel(world_x, world_y, z, voxel_color, spec.name))
    return voxels


def build_building_voxels(
    cell: ParsedCell,
    profile: BuildingProfile,
    resolved_elevation: int,
    spec: MaterialSpec,
    elevation_scale: int,
) -> list[Voxel]:
    voxels: list[Voxel] = []
    base_height = resolved_elevation * elevation_scale
    target_layers = max(SHAPE_HEIGHTS["wall"], min(SHAPE_HEIGHTS["roof"], len(profile.bands) + 1))
    world_start = profile.span[0] * SUBVOXEL_SCALE
    world_end = (profile.span[1] + 1) * SUBVOXEL_SCALE - 1
    world_center = (world_start + world_end) / 2

    for block_index in range(SUBVOXEL_SCALE * SUBVOXEL_SCALE):
        if all(band.block_coverage[block_index] == 0 for band in profile.bands):
            continue
        world_x = cell.x * SUBVOXEL_SCALE + (block_index % SUBVOXEL_SCALE)
        world_y = cell.y * SUBVOXEL_SCALE + (block_index // SUBVOXEL_SCALE)
        for layer_index in range(target_layers):
            source = profile.bands[min(layer_index, len(profile.bands) - 1)]
            if source.block_coverage[block_index] == 0:
                continue
            z = base_height + layer_index
            color = material_color(spec, source.block_colors[block_index])
            if layer_index == target_layers - 1 and profile.span[1] > profile.span[0]:
                span_radius = max(1.0, (world_end - world_start) / 2)
                distance = abs(world_x - world_center)
                if distance < span_radius * 0.6:
                    voxels.append(Voxel(world_x, world_y, z + 1, color, spec.name))
            voxels.append(Voxel(world_x, world_y, z, color, spec.name))
    return voxels


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
    if cell.elevation != ELEVATION_TRANSITION:
        return False
    return is_walkable_transition(cell)


def prefers_raised_level(cell: ParsedCell, material_name: str) -> bool:
    return (
        cell.elevation == ELEVATION_MULTI_LEVEL
        or material_name == "structure"
        or bool(cell.collision)
        or cell.layer_type != 0
    )


def is_walkable_transition(cell: ParsedCell) -> bool:
    if cell.collision or cell.layer_type != 0:
        return False
    return not any(token in cell.behavior_name for token in STRUCTURE_BEHAVIOR_TOKENS)


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
        for _block_y in range(SUBVOXEL_SCALE):
            for block_x in range(SUBVOXEL_SCALE):
                t = block_x / max(1, SUBVOXEL_SCALE - 1)
                level = start + (end - start) * t
                heights.append(round(level * elevation_scale + top_offset))
    else:
        start = north
        end = south
        for block_y in range(SUBVOXEL_SCALE):
            t = block_y / max(1, SUBVOXEL_SCALE - 1)
            level = start + (end - start) * t
            for _block_x in range(SUBVOXEL_SCALE):
                heights.append(round(level * elevation_scale + top_offset))
    return tuple(heights)


def inclusive_height_range(top_height: int) -> range:
    bottom = min(0, top_height)
    top = max(0, top_height)
    return range(bottom, top + 1)


def is_walkable_cell(cell: ParsedCell, material_name: str) -> bool:
    if material_name == "water":
        return False
    if cell.collision or cell.layer_type != 0:
        return False
    return not any(token in cell.behavior_name for token in STRUCTURE_BEHAVIOR_TOKENS)


def is_structure_candidate(cell: ParsedCell, material_name: str) -> bool:
    if material_name == "water":
        return False
    if any(token in cell.behavior_name for token in RAMP_BEHAVIOR_TOKENS):
        return False
    return material_name == "structure" or cell.layer_type != 0 or bool(cell.collision)


def is_door_anchor(
    cell: ParsedCell,
    material_name: str,
    direction: tuple[int, int] | None,
) -> bool:
    if not is_structure_candidate(cell, material_name):
        return False
    if not any(token in cell.behavior_name for token in STRUCTURE_BEHAVIOR_TOKENS):
        return False
    return direction is not None


def detect_building_direction(
    cell: ParsedCell,
    materials_by_cell: dict[tuple[int, int], str],
    cells_by_pos: dict[tuple[int, int], ParsedCell],
) -> tuple[int, int] | None:
    best_direction: tuple[int, int] | None = None
    best_length = 0
    for direction in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        length = 0
        for depth in range(MAX_BUILDING_BANDS):
            candidate = cells_by_pos.get((cell.x + direction[0] * depth, cell.y + direction[1] * depth))
            if candidate is None:
                break
            material_name = materials_by_cell[(candidate.x, candidate.y)]
            if not is_structure_candidate(candidate, material_name):
                break
            length += 1
        if length > best_length:
            best_length = length
            best_direction = direction
    if best_length < 2:
        return None
    return best_direction


def detect_front_span(
    anchor: ParsedCell,
    direction: tuple[int, int],
    materials_by_cell: dict[tuple[int, int], str],
    cells_by_pos: dict[tuple[int, int], ParsedCell],
) -> tuple[int, int]:
    horizontal = direction[0] == 0
    start = end = anchor.x if horizontal else anchor.y
    step_axis = ((-1, 0), (1, 0)) if horizontal else ((0, -1), (0, 1))
    for dx, dy in step_axis:
        x = anchor.x
        y = anchor.y
        while True:
            x += dx
            y += dy
            candidate = cells_by_pos.get((x, y))
            if candidate is None:
                break
            material_name = materials_by_cell[(candidate.x, candidate.y)]
            if not is_front_face_candidate(candidate, direction, material_name, materials_by_cell, cells_by_pos):
                break
            axis_value = x if horizontal else y
            if dx < 0 or dy < 0:
                start = axis_value
            else:
                end = axis_value
    return start, end


def is_front_face_candidate(
    cell: ParsedCell,
    direction: tuple[int, int],
    material_name: str,
    materials_by_cell: dict[tuple[int, int], str],
    cells_by_pos: dict[tuple[int, int], ParsedCell],
) -> bool:
    if not is_structure_candidate(cell, material_name):
        return False
    next_band = cells_by_pos.get((cell.x + direction[0], cell.y + direction[1]))
    if next_band is None:
        return False
    next_material = materials_by_cell[(next_band.x, next_band.y)]
    return is_structure_candidate(next_band, next_material)


def measure_building_bands(
    front_cell: ParsedCell,
    direction: tuple[int, int],
    materials_by_cell: dict[tuple[int, int], str],
    cells_by_pos: dict[tuple[int, int], ParsedCell],
) -> list[ParsedCell]:
    bands: list[ParsedCell] = []
    last_signature: tuple[int, ...] | None = None
    repeated_rows = 0
    for depth in range(MAX_BUILDING_BANDS):
        candidate = cells_by_pos.get(
            (front_cell.x + direction[0] * depth, front_cell.y + direction[1] * depth)
        )
        if candidate is None:
            break
        material_name = materials_by_cell[(candidate.x, candidate.y)]
        if not is_structure_candidate(candidate, material_name):
            break
        signature = tuple(color_signature(color) for color in candidate.block_colors)
        if signature == last_signature:
            repeated_rows += 1
            if repeated_rows >= 2:
                break
        else:
            repeated_rows = 0
        last_signature = signature
        bands.append(candidate)
        if sum(candidate.row_coverage) <= 8:
            break
    return bands


def front_positions_for_span(
    anchor: ParsedCell,
    direction: tuple[int, int],
    span: tuple[int, int],
) -> list[tuple[int, int]]:
    if direction[0] == 0:
        return [(x, anchor.y) for x in range(span[0], span[1] + 1)]
    return [(anchor.x, y) for y in range(span[0], span[1] + 1)]


def is_void_tile(cell: ParsedCell) -> bool:
    if sum(cell.row_coverage) == 0:
        return True
    opaque = [color for color in cell.block_colors if color[3]]
    return bool(opaque) and all(color[:3] == (0, 0, 0) for color in opaque)


def is_sparse_object_candidate(cell: ParsedCell, material_name: str) -> bool:
    if material_name == "water":
        return False
    if any(token in cell.behavior_name for token in RAMP_BEHAVIOR_TOKENS):
        return False
    opaque_pixels = sum(cell.row_coverage)
    if opaque_pixels == 0:
        return False
    if any(token in cell.behavior_name for token in OBJECT_BEHAVIOR_TOKENS):
        return True
    if cell.layer_type != 0 and opaque_pixels < 220:
        return True
    return opaque_pixels < 170 and any(coverage < 16 for coverage in cell.block_coverage)


def measured_object_height(cell: ParsedCell) -> int:
    for row, coverage in enumerate(cell.row_coverage):
        if coverage:
            return max(1, min(SHAPE_HEIGHTS["tree"], ((16 - row) + 3) // 4))
    return 1


def neighbor_cliff_height(
    resolved_elevations: dict[tuple[int, int], int],
    cell: ParsedCell,
    neighbor_levels: dict[str, int],
) -> int:
    here = resolved_elevations[(cell.x, cell.y)]
    if not neighbor_levels:
        return 0
    return max(abs(here - level) for level in neighbor_levels.values())


def summarize_shapes(shapes: dict[tuple[int, int], ShapeRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for shape in shapes.values():
        counts[shape.shape_class] = counts.get(shape.shape_class, 0) + 1
    return counts


def color_signature(color: Color) -> int:
    return ((color[0] // 32) << 10) | ((color[1] // 32) << 5) | (color[2] // 32)
