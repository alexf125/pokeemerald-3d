from __future__ import annotations

import base64
import json
import struct
from dataclasses import asdict
from pathlib import Path

from .generator import MATERIAL_SPECS, MaterialSpec, Voxel, VoxelModel

FACE_SHADE = {
    (0, 0, 1): 1.00,
    (0, 1, 0): 0.90,
    (1, 0, 0): 0.84,
    (-1, 0, 0): 0.72,
    (0, -1, 0): 0.68,
    (0, 0, -1): 0.55,
}


def export_json(model: VoxelModel, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "map_name": model.map_name,
        "dimensions": {"x": model.size_x, "y": model.size_y, "z": model.size_z},
        "metadata": model.metadata,
        "materials": {
            name: {
                "fallback_color": spec.fallback_color,
                "alpha": spec.alpha,
                "height_bonus": spec.height_bonus,
            }
            for name, spec in model.materials.items()
        },
        "voxels": [asdict(voxel) for voxel in model.voxels],
    }
    output_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return output_path


def export_vox(model: VoxelModel, output_path: Path) -> Path:
    if max(model.size_x, model.size_y, model.size_z) > 255:
        raise ValueError("MagicaVoxel export only supports dimensions up to 255 in this tool")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    palette: dict[tuple[int, int, int, int], int] = {}
    palette_entries: list[tuple[int, int, int, int]] = []
    xyzi = bytearray()
    for voxel in model.voxels:
        color = quantize_vox_color(voxel.color)
        index = palette.get(color)
        if index is None:
            if len(palette_entries) >= 255:
                raise ValueError("Too many palette entries for VOX export")
            palette_entries.append(color)
            index = len(palette_entries)
            palette[color] = index
        xyzi.extend(bytes((voxel.x, voxel.z, voxel.y, index)))

    size_chunk = _vox_chunk("SIZE", struct.pack("<III", model.size_x, model.size_z, model.size_y))
    xyzi_chunk = _vox_chunk("XYZI", struct.pack("<I", len(model.voxels)) + bytes(xyzi))
    rgba = bytearray()
    for index in range(256):
        color = palette_entries[index] if index < len(palette_entries) else (0, 0, 0, 255)
        rgba.extend(struct.pack("<BBBB", *color))
    rgba_chunk = _vox_chunk("RGBA", bytes(rgba))
    main_children = size_chunk + xyzi_chunk + rgba_chunk
    vox = b"VOX " + struct.pack("<I", 150) + _vox_chunk("MAIN", b"", main_children)
    output_path.write_bytes(vox)
    return output_path


def export_gltf(model: VoxelModel, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document, binary_blob = build_gltf_document(model, embed_binary=True)
    document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(binary_blob).decode("ascii")
    output_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return output_path


def export_glb(model: VoxelModel, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document, binary_blob = build_gltf_document(model, embed_binary=False)
    json_chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    binary_chunk = binary_blob + (b"\x00" * ((4 - len(binary_blob) % 4) % 4))
    total_length = 12 + 8 + len(json_chunk) + 8 + len(binary_chunk)
    glb = bytearray()
    glb.extend(struct.pack("<4sII", b"glTF", 2, total_length))
    glb.extend(struct.pack("<I4s", len(json_chunk), b"JSON"))
    glb.extend(json_chunk)
    glb.extend(struct.pack("<I4s", len(binary_chunk), b"BIN\x00"))
    glb.extend(binary_chunk)
    output_path.write_bytes(glb)
    return output_path


def build_gltf_document(model: VoxelModel, embed_binary: bool) -> tuple[dict[str, object], bytes]:
    material_specs = effective_materials(model)
    buffers = bytearray()
    buffer_views: list[dict[str, object]] = []
    accessors: list[dict[str, object]] = []
    materials = [
        {
            "name": spec.name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [1.0, 1.0, 1.0, round(spec.alpha / 255, 4)],
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
            "alphaMode": "BLEND" if spec.alpha < 255 else "OPAQUE",
            "doubleSided": True,
        }
        for spec in material_specs.values()
    ]
    material_indices = {name: index for index, name in enumerate(material_specs)}

    primitives = []
    for material_name, mesh in build_mesh_by_material(model, material_specs).items():
        if not mesh["positions"]:
            continue
        position_accessor = _append_accessor(
            buffers,
            buffer_views,
            accessors,
            struct.pack(f"<{len(mesh['positions'])}f", *mesh["positions"]),
            component_type=5126,
            type_name="VEC3",
            count=len(mesh["positions"]) // 3,
            target=34962,
            min_values=mesh["position_min"],
            max_values=mesh["position_max"],
        )
        normal_accessor = _append_accessor(
            buffers,
            buffer_views,
            accessors,
            struct.pack(f"<{len(mesh['normals'])}f", *mesh["normals"]),
            component_type=5126,
            type_name="VEC3",
            count=len(mesh["normals"]) // 3,
            target=34962,
        )
        color_accessor = _append_accessor(
            buffers,
            buffer_views,
            accessors,
            struct.pack(f"<{len(mesh['colors'])}f", *mesh["colors"]),
            component_type=5126,
            type_name="VEC4",
            count=len(mesh["colors"]) // 4,
            target=34962,
        )
        index_accessor = _append_accessor(
            buffers,
            buffer_views,
            accessors,
            struct.pack(f"<{len(mesh['indices'])}I", *mesh["indices"]),
            component_type=5125,
            type_name="SCALAR",
            count=len(mesh["indices"]),
            target=34963,
            min_values=[min(mesh["indices"])],
            max_values=[max(mesh["indices"])],
        )
        primitives.append(
            {
                "attributes": {
                    "POSITION": position_accessor,
                    "NORMAL": normal_accessor,
                    "COLOR_0": color_accessor,
                },
                "indices": index_accessor,
                "material": material_indices[material_name],
            }
        )

    document: dict[str, object] = {
        "asset": {"version": "2.0", "generator": "tools/voxel_converter"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": model.map_name}],
        "meshes": [{"name": model.map_name, "primitives": primitives}],
        "materials": materials,
        "buffers": [{"byteLength": len(buffers)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    if not embed_binary:
        document["buffers"][0].pop("uri", None)
    return document, bytes(buffers)


MeshData = dict[str, list[float] | list[int]]


def build_mesh_by_material(
    model: VoxelModel,
    materials: dict[str, MaterialSpec],
) -> dict[str, MeshData]:
    occupancy = {(voxel.x, voxel.y, voxel.z): voxel for voxel in model.voxels}
    mesh_by_material: dict[str, MeshData] = {
        name: {
            "positions": [],
            "normals": [],
            "colors": [],
            "indices": [],
            "position_min": [float("inf"), float("inf"), float("inf")],
            "position_max": [float("-inf"), float("-inf"), float("-inf")],
        }
        for name in materials
    }

    faces = {
        (1, 0, 0): [(1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)],
        (-1, 0, 0): [(0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)],
        (0, 1, 0): [(0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)],
        (0, -1, 0): [(0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0)],
        (0, 0, 1): [(0, 0, 1), (0, 1, 1), (1, 1, 1), (1, 0, 1)],
        (0, 0, -1): [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
    }

    for voxel in model.voxels:
        mesh = mesh_by_material[voxel.material]
        for normal, corners in faces.items():
            neighbor = (voxel.x + normal[0], voxel.y + normal[1], voxel.z + normal[2])
            if neighbor in occupancy:
                continue
            base_index = len(mesh["positions"]) // 3
            for corner in corners:
                shaded_color = shaded_vertex_color(occupancy, voxel, normal, corner)
                position = (voxel.x + corner[0], voxel.z + corner[2], voxel.y + corner[1])
                mesh["positions"].extend(position)
                mesh["normals"].extend((normal[0], normal[2], normal[1]))
                mesh["colors"].extend(shaded_color)
                for axis in range(3):
                    mesh["position_min"][axis] = min(mesh["position_min"][axis], position[axis])
                    mesh["position_max"][axis] = max(mesh["position_max"][axis], position[axis])
            mesh["indices"].extend((base_index, base_index + 1, base_index + 2, base_index, base_index + 2, base_index + 3))

    return mesh_by_material


def effective_materials(model: VoxelModel) -> dict[str, MaterialSpec]:
    materials = dict(model.materials)
    for voxel in model.voxels:
        if voxel.material not in materials:
            materials[voxel.material] = MATERIAL_SPECS.get(
                voxel.material, MATERIAL_SPECS["ground"]
            )
    return materials


def _append_accessor(
    buffers: bytearray,
    buffer_views: list[dict[str, object]],
    accessors: list[dict[str, object]],
    blob: bytes,
    *,
    component_type: int,
    type_name: str,
    count: int,
    target: int,
    min_values: list[float | int] | None = None,
    max_values: list[float | int] | None = None,
) -> int:
    while len(buffers) % 4:
        buffers.append(0)
    offset = len(buffers)
    buffers.extend(blob)
    view_index = len(buffer_views)
    buffer_views.append(
        {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(blob),
            "target": target,
        }
    )
    accessor: dict[str, object] = {
        "bufferView": view_index,
        "componentType": component_type,
        "count": count,
        "type": type_name,
    }
    if min_values is not None:
        accessor["min"] = min_values
    if max_values is not None:
        accessor["max"] = max_values
    accessors.append(accessor)
    return len(accessors) - 1


def _vox_chunk(tag: str, content: bytes, children: bytes = b"") -> bytes:
    return tag.encode("ascii") + struct.pack("<II", len(content), len(children)) + content + children


def quantize_vox_color(color: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    quantized = tuple(min(255, round(channel / 51) * 51) for channel in color[:3])
    return (quantized[0], quantized[1], quantized[2], 255)


def shaded_vertex_color(
    occupancy: dict[tuple[int, int, int], Voxel],
    voxel: Voxel,
    normal: tuple[int, int, int],
    corner: tuple[int, int, int],
) -> list[float]:
    face_shade = FACE_SHADE.get(normal, 1.0)
    occlusion = ambient_occlusion(occupancy, voxel, normal, corner)
    crease = 0.92 if normal[2] == 0 and voxel.z <= 1 else 1.0
    shade = max(0.0, min(1.0, face_shade * occlusion * crease))
    return [
        min(1.0, (voxel.color[0] / 255) * shade),
        min(1.0, (voxel.color[1] / 255) * shade),
        min(1.0, (voxel.color[2] / 255) * shade),
        voxel.color[3] / 255,
    ]


def ambient_occlusion(
    occupancy: dict[tuple[int, int, int], Voxel],
    voxel: Voxel,
    normal: tuple[int, int, int],
    corner: tuple[int, int, int],
) -> float:
    tangent_axes = [axis for axis, delta in enumerate(normal) if delta == 0]
    if len(tangent_axes) != 2:
        return 1.0
    side_offsets: list[tuple[int, int, int]] = []
    for axis in tangent_axes:
        direction = -1 if corner[axis] == 0 else 1
        offset = [0, 0, 0]
        offset[axis] = direction
        side_offsets.append(tuple(offset))
    side_a = has_neighbor(occupancy, voxel, side_offsets[0])
    side_b = has_neighbor(occupancy, voxel, side_offsets[1])
    diagonal = has_neighbor(
        occupancy,
        voxel,
        tuple(side_offsets[0][axis] + side_offsets[1][axis] for axis in range(3)),
    )
    if side_a and side_b:
        return 0.72
    blocked = int(side_a) + int(side_b) + int(diagonal)
    return 1.0 - blocked * 0.1


def has_neighbor(
    occupancy: dict[tuple[int, int, int], Voxel],
    voxel: Voxel,
    offset: tuple[int, int, int],
) -> bool:
    return (voxel.x + offset[0], voxel.y + offset[1], voxel.z + offset[2]) in occupancy
