from __future__ import annotations

import json
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from tools.voxel_converter.convert import main as convert_main
from tools.voxel_converter.exporters import export_glb, export_gltf, export_json, export_vox
from tools.voxel_converter.generator import SUBVOXEL_SCALE, Voxel, VoxelModel, generate_voxel_model
from tools.voxel_converter.parser import (
    find_repo_root,
    load_behavior_material_names,
    parse_behavior_definitions,
    parse_map,
)


class VoxelConverterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = find_repo_root(Path(__file__).resolve())

    def test_parse_behavior_definitions(self) -> None:
        behaviors = parse_behavior_definitions(
            self.repo_root / "include/constants/metatile_behaviors.h"
        )
        self.assertEqual(behaviors["MB_NORMAL"], 0)
        self.assertEqual(behaviors["MB_TALL_GRASS"], 2)
        self.assertIn("MB_OCEAN_WATER", behaviors)

    def test_parse_littleroot_map(self) -> None:
        parsed = parse_map(self.repo_root, "LittlerootTown")
        self.assertEqual(parsed.map_name, "LittlerootTown")
        self.assertEqual(parsed.width, 20)
        self.assertEqual(parsed.height, 20)
        self.assertEqual(len(parsed.cells), 400)
        self.assertTrue(any(cell.behavior_name.startswith("MB_") for cell in parsed.cells))
        self.assertTrue(all(len(cell.block_colors) == 16 for cell in parsed.cells))
        self.assertTrue(all(len(cell.block_coverage) == 16 for cell in parsed.cells))
        self.assertTrue(all(len(cell.block_top_rows) == 16 for cell in parsed.cells))
        self.assertTrue(all(len(cell.row_coverage) == 16 for cell in parsed.cells))

    def test_generate_subvoxel_heights_for_buildings_and_multi_level_maps(self) -> None:
        behavior_materials = load_behavior_material_names(
            self.repo_root / "tools/voxel_converter/behavior_map.json"
        )

        littleroot = generate_voxel_model(
            parse_map(self.repo_root, "LittlerootTown"),
            behavior_materials,
        )
        self.assertEqual(littleroot.size_x, 80)
        self.assertEqual(littleroot.size_y, 80)
        # LittlerootTown house door at (5, 8) should sit above the ground tile
        # directly in front of it at (5, 7).
        self.assertGreater(
            cell_top_height(littleroot, 5, 8),
            cell_top_height(littleroot, 5, 7),
        )

        sootopolis = generate_voxel_model(
            parse_map(self.repo_root, "SootopolisCity"),
            behavior_materials,
        )
        # Sootopolis upper platform near the gym/cave approach should remain
        # above the lower water ring.
        self.assertGreater(
            cell_top_height(sootopolis, 31, 33),
            cell_top_height(sootopolis, 31, 41),
        )

        fortree = generate_voxel_model(
            parse_map(self.repo_root, "FortreeCity"),
            behavior_materials,
        )
        # Fortree bridge spans around (30, 14) should resolve as a distinct
        # walkable level without exploding into the raw elevation-15 spike.
        self.assertNotEqual(
            cell_top_height(fortree, 30, 14),
            cell_top_height(fortree, 29, 14),
        )
        self.assertLess(cell_top_height(fortree, 30, 14), 12)

    def test_generate_gradual_stair_heights(self) -> None:
        behavior_materials = load_behavior_material_names(
            self.repo_root / "tools/voxel_converter/behavior_map.json"
        )
        route108 = generate_voxel_model(
            parse_map(self.repo_root, "Route108"),
            behavior_materials,
        )
        # The Route 108 abandoned ship stairs should preserve a local gradient.
        stair_heights = block_top_heights(route108, 29, 6)
        self.assertGreater(len(set(stair_heights)), 1)
        self.assertGreater(max(stair_heights), min(stair_heights))

    def test_building_fronts_fold_north_rows_into_raised_facades(self) -> None:
        behavior_materials = load_behavior_material_names(
            self.repo_root / "tools/voxel_converter/behavior_map.json"
        )
        littleroot = generate_voxel_model(
            parse_map(self.repo_root, "LittlerootTown"),
            behavior_materials,
        )
        self.assertGreater(cell_top_height(littleroot, 5, 8), 10)
        self.assertFalse(cell_has_voxels(littleroot, 5, 7))
        self.assertFalse(cell_has_voxels(littleroot, 5, 6))
        self.assertIn("building", littleroot.metadata["shape_counts"])

    def test_exporters_and_cli(self) -> None:
        model = VoxelModel(
            map_name="UnitTestMap",
            size_x=2,
            size_y=2,
            size_z=2,
            voxels=(
                Voxel(0, 0, 0, (255, 0, 0, 255), "ground"),
                Voxel(1, 0, 0, (0, 0, 255, 160), "water"),
            ),
            materials={},
            metadata={"source": "unit-test"},
        )
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir_path = Path(tempdir)
            json_path = export_json(model, tempdir_path / "unit.json")
            vox_path = export_vox(model, tempdir_path / "unit.vox")
            gltf_path = export_gltf(model, tempdir_path / "unit.gltf")
            glb_path = export_glb(model, tempdir_path / "unit.glb")

            self.assertEqual(vox_path.read_bytes()[:4], b"VOX ")
            self.assertEqual(glb_path.read_bytes()[:4], b"glTF")
            gltf_doc = json.loads(gltf_path.read_text(encoding="utf-8"))
            self.assertEqual(gltf_doc["asset"]["version"], "2.0")
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["map_name"], "UnitTestMap")

            exit_code = convert_main(
                [
                    "LittlerootTown",
                    "--format",
                    "json",
                    "--output-dir",
                    tempdir,
                    "--repo-root",
                    str(self.repo_root),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue((tempdir_path / "LittlerootTown.json").exists())


def cell_top_height(model: VoxelModel, cell_x: int, cell_y: int) -> int:
    return max(block_top_heights(model, cell_x, cell_y))


def block_top_heights(model: VoxelModel, cell_x: int, cell_y: int) -> list[int]:
    tops: dict[tuple[int, int], int] = defaultdict(lambda: -1)
    for voxel in model.voxels:
        if (
            cell_x * SUBVOXEL_SCALE <= voxel.x < cell_x * SUBVOXEL_SCALE + SUBVOXEL_SCALE
            and cell_y * SUBVOXEL_SCALE <= voxel.y < cell_y * SUBVOXEL_SCALE + SUBVOXEL_SCALE
        ):
            local_x = voxel.x - cell_x * SUBVOXEL_SCALE
            local_y = voxel.y - cell_y * SUBVOXEL_SCALE
            tops[(local_x, local_y)] = max(tops[(local_x, local_y)], voxel.z)
    return [
        tops[(x, y)]
        for y in range(SUBVOXEL_SCALE)
        for x in range(SUBVOXEL_SCALE)
    ]


def cell_has_voxels(model: VoxelModel, cell_x: int, cell_y: int) -> bool:
    return any(
        cell_x * SUBVOXEL_SCALE <= voxel.x < cell_x * SUBVOXEL_SCALE + SUBVOXEL_SCALE
        and cell_y * SUBVOXEL_SCALE <= voxel.y < cell_y * SUBVOXEL_SCALE + SUBVOXEL_SCALE
        for voxel in model.voxels
    )


if __name__ == "__main__":
    unittest.main()
