from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vercel_app.material_blob import PrivateMaterialBlobStore
from vercel_app.material_library_local import local_material_database_path, local_material_library_root
from vercel_app.similarity_store import _connection, _ensure_schema


class LocalMaterialBundleTest(unittest.TestCase):
    def test_explicit_bundle_overrides_remote_database_and_blob_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "LOCAL_MATERIAL_LIBRARY_DIR": directory,
                "MATERIAL_BLOB_READ_WRITE_TOKEN": "cloud-token-that-must-not-be-used",
            },
            clear=False,
        ), patch("vercel_app.similarity_store.database_url", return_value="postgresql://remote.invalid/db"):
            database = Path(directory).resolve() / "materials.sqlite3"
            self.assertEqual(local_material_database_path(), database)
            with _connection() as connection:
                _ensure_schema(connection)
                connection.execute(
                    "INSERT INTO material_libraries (id, token_hash, created_at) VALUES (?, ?, ?)",
                    ("local", "hash", 1.0),
                )
                connection.commit()
            self.assertTrue(database.is_file())

            store = PrivateMaterialBlobStore()
            self.assertTrue(store.local)
            reference = store.put("material-search/local/example.bin", b"local", "application/octet-stream")
            self.assertEqual(store.read(reference)[0], b"local")

    def test_explicit_local_data_directory_disables_auto_detected_bundle(self) -> None:
        project_bundle = Path(__file__).resolve().parent.parent / "var" / "material-library"
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"LOCAL_DATA_DIR": directory, "LOCAL_MATERIAL_LIBRARY_DIR": ""},
            clear=False,
        ):
            self.assertIsNone(local_material_library_root())
            self.assertNotEqual(local_material_database_path(), project_bundle / "materials.sqlite3")


if __name__ == "__main__":
    unittest.main()
