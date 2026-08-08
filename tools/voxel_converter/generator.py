from __future__ import annotations

from dataclasses import dataclass

from .parser import Color, ParsedMap

DEFAULT_BEHAVIOR_MATERIAL = "ground"


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

    for cell in parsed_map.cells:
        material_name = resolve_material(cell.behavior_name, cell.layer_type, cell.collision, behavior_materials)
        spec = MATERIAL_SPECS.get(material_name, MATERIAL_SPECS[DEFAULT_BEHAVIOR_MATERIAL])
        used_materials[spec.name] = spec
        top_height = cell.elevation * elevation_scale + spec.height_bonus
        max_height = max(max_height, top_height)
        for quadrant, color in enumerate(cell.quadrant_colors):
            world_x = cell.x * 2 + (quadrant % 2)
            world_y = cell.y * 2 + (quadrant // 2)
            voxel_color = material_color(spec, color)
            for z in range(top_height + 1):
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
        "cell_width": parsed_map.width,
        "cell_height": parsed_map.height,
    }
    return VoxelModel(
        map_name=parsed_map.map_name,
        size_x=parsed_map.width * 2,
        size_y=parsed_map.height * 2,
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
    if layer_type != 0 or collision:
        return "structure"
    return DEFAULT_BEHAVIOR_MATERIAL


def material_color(spec: MaterialSpec, source_color: Color) -> Color:
    if source_color[3]:
        return (source_color[0], source_color[1], source_color[2], spec.alpha)
    fallback = spec.fallback_color
    return (fallback[0], fallback[1], fallback[2], spec.alpha)
