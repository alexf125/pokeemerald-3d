from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.voxel_converter.convert import main as convert_main
from tools.voxel_converter.exporters import export_glb, export_gltf, export_json, export_vox
from tools.voxel_converter.generator import Voxel, VoxelModel
from tools.voxel_converter.parser import find_repo_root, parse_behavior_definitions, parse_map


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


if __name__ == "__main__":
    unittest.main()
