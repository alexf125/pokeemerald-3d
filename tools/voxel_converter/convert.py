from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.voxel_converter.exporters import export_glb, export_gltf, export_json, export_vox
from tools.voxel_converter.generator import generate_voxel_model
from tools.voxel_converter.parser import (
    find_repo_root,
    load_behavior_material_names,
    parse_behavior_definitions,
    parse_map,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert pokeemerald-3d maps into voxel geometry")
    parser.add_argument("map_name", help="Map directory, map name, or map id (for example LittlerootTown)")
    parser.add_argument(
        "--format",
        action="append",
        choices=("vox", "gltf", "glb", "json", "all"),
        dest="formats",
        help="Output format(s). Repeat to request multiple formats. Default: vox",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for generated files. Default: <repo>/build/voxels",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root. Default: auto-detect from this script",
    )
    parser.add_argument(
        "--elevation-scale",
        type=int,
        default=2,
        help="Voxel units per elevation level. Default: 2",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    repo_root = find_repo_root(args.repo_root)
    output_dir = args.output_dir or (repo_root / "build/voxels")
    formats = normalize_formats(args.formats or ["vox"])

    behavior_names = parse_behavior_definitions(repo_root / "include/constants/metatile_behaviors.h")
    behavior_materials = load_behavior_material_names(
        repo_root / "tools/voxel_converter/behavior_map.json"
    )

    parsed = parse_map(repo_root, args.map_name, {value: name for name, value in behavior_names.items()})
    model = generate_voxel_model(parsed, behavior_materials, elevation_scale=args.elevation_scale)

    outputs = []
    for output_format in formats:
        output_path = output_dir / f"{parsed.map_name}.{output_format}"
        if output_format == "vox":
            export_vox(model, output_path)
        elif output_format == "gltf":
            export_gltf(model, output_path)
        elif output_format == "glb":
            export_glb(model, output_path)
        else:
            export_json(model, output_path)
        outputs.append(str(output_path))

    summary = {
        "map_name": parsed.map_name,
        "layout_id": parsed.layout_id,
        "dimensions": {"x": model.size_x, "y": model.size_y, "z": model.size_z},
        "voxel_count": len(model.voxels),
        "outputs": outputs,
    }
    print(json.dumps(summary, indent=2))
    return 0


def normalize_formats(formats: list[str]) -> list[str]:
    if "all" in formats:
        return ["vox", "gltf", "glb", "json"]
    deduped: list[str] = []
    for item in formats:
        if item not in deduped:
            deduped.append(item)
    return deduped


if __name__ == "__main__":
    raise SystemExit(main())
