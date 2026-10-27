from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image, ImageDraw


os.environ["LOCAL_DATA_DIR"] = tempfile.mkdtemp(prefix="lingjie-material-test-")
os.environ["ADMIN_USERNAME"] = "similarity-admin"
os.environ["ADMIN_PASSWORD"] = "similarity-password"
os.environ["ADMIN_NAME"] = "素材检索管理员"

from fastapi.testclient import TestClient
from app import app
from vercel_app.similarity_api import _search_materials
from vercel_app.similarity_engine import image_geometric_similarity, process_image
from vercel_app.similarity_store import _connection, _ensure_schema


class SimilaritySearchEndToEndTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        logged_in = self.client.post("/api/auth/login", json={
            "username": "similarity-admin", "password": "similarity-password",
        })
        self.assertEqual(logged_in.status_code, 200)
        library = self.client.post("/api/material-search/libraries").json()
        self.headers = {
            "X-Material-Library": library["library_id"],
            "X-Material-Token": library["access_token"],
        }

    @staticmethod
    def fixtures() -> dict[str, bytes]:
        image = Image.new("RGB", (240, 160), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((35, 30, 205, 130), outline="black", width=8)
        draw.ellipse((90, 48, 150, 108), fill="#2f6b57")
        png = io.BytesIO()
        image.save(png, "PNG")
        pdf = io.BytesIO()
        image.save(pdf, "PDF")
        dxf = b"""0
SECTION
2
ENTITIES
0
LINE
10
0
20
0
11
100
21
0
0
LINE
10
100
20
0
11
100
21
60
0
CIRCLE
10
50
20
30
40
15
0
ENDSEC
0
EOF
"""
        return {"preview.png": png.getvalue(), "drawing.dxf": dxf, "design.ai": pdf.getvalue()}

    def test_legacy_material_database_is_migrated_per_file(self) -> None:
        for _ in range(2):
            database_path = os.path.join(tempfile.mkdtemp(prefix="lingjie-legacy-material-"), "image3d.sqlite3")
            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                CREATE TABLE material_folders (
                    id TEXT PRIMARY KEY,
                    library_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    zip_url TEXT,
                    created_at DOUBLE PRECISION NOT NULL,
                    updated_at DOUBLE PRECISION NOT NULL
                )
                """
            )
            _ensure_schema(connection)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(material_folders)")}
            connection.close()
            self.assertIn("deleted_at", columns)

    def test_all_visitors_receive_the_same_public_library(self) -> None:
        first = self.client.post("/api/material-search/libraries").json()
        second = self.client.post("/api/material-search/libraries").json()
        self.assertEqual(first, second)

        headers = {
            "X-Material-Library": second["library_id"],
            "X-Material-Token": second["access_token"],
        }
        folder = self.client.post(
            "/api/material-search/folders", headers=self.headers, json={"name": "shared-folder"}
        ).json()
        visible_ids = {
            item["id"]
            for item in self.client.get("/api/material-search/folders", headers=headers).json()["folders"]
        }
        self.assertIn(folder["id"], visible_ids)
        self.assertEqual(
            self.client.delete(f"/api/material-search/folders/{folder['id']}", headers=headers).status_code,
            204,
        )
        self.assertEqual(
            self.client.delete(f"/api/material-search/trash/{folder['id']}", headers=headers).status_code,
            204,
        )

    def test_sift_ransac_recognizes_a_design_inside_a_scene(self) -> None:
        reference = Image.new("RGB", (500, 500), "white")
        draw = ImageDraw.Draw(reference)
        draw.ellipse((155, 135, 345, 325), outline="black", width=13)
        draw.ellipse((198, 185, 242, 235), fill="black")
        draw.ellipse((258, 185, 302, 235), fill="black")
        draw.polygon([(250, 220), (225, 270), (275, 270)], fill="black")
        for offset in range(0, 8):
            y = 80 + offset * 34
            draw.line((35, 250, 165, y), fill="black", width=8 + offset % 3)
            draw.line((335, y, 465, 250), fill="black", width=8 + (7 - offset) % 3)
        for offset in range(7):
            x = 155 + offset * 31
            draw.arc((x, 285, x + 70, 425), 20, 165, fill="black", width=7)
        reference_buffer = io.BytesIO()
        reference.save(reference_buffer, "PNG")

        source = cv2.cvtColor(np.asarray(reference), cv2.COLOR_RGB2BGR)
        destination = np.float32([[80, 55], [520, 90], [500, 535], [55, 505]])
        transform = cv2.getPerspectiveTransform(np.float32([[0, 0], [499, 0], [499, 499], [0, 499]]), destination)
        scene = cv2.warpPerspective(source, transform, (580, 580), borderValue=(190, 205, 215))
        scene = cv2.convertScaleAbs(scene, alpha=0.82, beta=18)
        scene_buffer = io.BytesIO()
        Image.fromarray(cv2.cvtColor(scene, cv2.COLOR_BGR2RGB)).save(scene_buffer, "JPEG", quality=88)

        distractor = Image.new("RGB", (500, 500), "white")
        distractor_draw = ImageDraw.Draw(distractor)
        for radius in range(40, 230, 24):
            distractor_draw.ellipse((250 - radius, 250 - radius, 250 + radius, 250 + radius), outline="black", width=7)
        distractor_buffer = io.BytesIO()
        distractor.save(distractor_buffer, "PNG")

        _, reference_vector, reference_metadata = process_image(reference_buffer.getvalue())
        _, query_vector, query_metadata = process_image(scene_buffer.getvalue())
        _, distractor_vector, distractor_metadata = process_image(distractor_buffer.getvalue())
        matching = image_geometric_similarity(query_metadata, reference_metadata, 0.5)
        non_matching = image_geometric_similarity(query_metadata, distractor_metadata, 0.5)
        self.assertGreaterEqual(matching["inliers"], 8)
        self.assertGreater(matching["score"], non_matching["score"] + 0.25)
        self.assertIn(matching["label"], {"可能同款", "高度可信"})

    def test_image_search_runs_geometry_only_for_cosine_top_ten(self) -> None:
        candidates = [
            {
                "folder_id": f"folder-{index}",
                "folder_name": f"folder-{index}",
                "asset_id": f"asset-{index}",
                "original_name": f"image-{index}.png",
                "preview_url": f"preview-{index}",
                "vector": [1.0, index / 10.0],
                "metadata": {"feature_version": "material-search-v3-perspective-sift"},
            }
            for index in range(12)
        ]

        def geometric(_query, _candidate, global_score):
            return {
                "score": global_score,
                "label": "可能同款",
                "inliers": 8,
                "inlier_ratio": 0.4,
                "coverage": 0.1,
                "method": "sift-ransac",
            }

        folder = {"previews": [], "assets": [], "zip_url": None}
        with (
            patch("vercel_app.similarity_api.process_file", return_value=("image", b"preview", [1.0, 0.0], {})),
            patch("vercel_app.similarity_api.searchable_assets", return_value=candidates),
            patch("vercel_app.similarity_api.get_folder", return_value=folder),
            patch("vercel_app.similarity_api.image_geometric_similarity", side_effect=geometric) as verify,
        ):
            payload = _search_materials("library", "query.png", b"query")

        self.assertEqual(len(payload["results"]), 10)
        self.assertEqual(verify.call_count, 10)
        self.assertEqual(payload["results"][0]["matched_asset_id"], "asset-0")
        self.assertEqual(payload["geometric_best"]["matched_asset_id"], "asset-0")
        self.assertEqual(payload["geometric_best"]["first_search_rank"], 1)

    def test_upload_preview_download_and_all_three_search_types(self) -> None:
        missing_name = self.client.post(
            "/api/material-search/folders", headers=self.headers, json={"name": "   "}
        )
        self.assertEqual(missing_name.status_code, 422)
        folder_response = self.client.post(
            "/api/material-search/folders", headers=self.headers, json={"name": "sample-folder"}
        )
        self.assertEqual(folder_response.status_code, 200)
        folder_id = folder_response.json()["id"]
        for name, raw in self.fixtures().items():
            if name == "preview.png":
                upload_id = "a" * 32
                midpoint = len(raw) // 2
                for chunk_index, chunk in enumerate((raw[:midpoint], raw[midpoint:])):
                    chunk_response = self.client.post(
                        f"/api/material-search/folders/{folder_id}/uploads/{upload_id}/chunks/{chunk_index}",
                        headers=self.headers,
                        files={"chunk": (f"{name}.part", chunk)},
                    )
                    self.assertEqual(chunk_response.status_code, 200, chunk_response.text)
                response = self.client.post(
                    f"/api/material-search/folders/{folder_id}/uploads/{upload_id}/complete",
                    headers=self.headers,
                    json={
                        "filename": name,
                        "relative_path": f"sample-folder/{name}",
                        "chunk_count": 2,
                    },
                )
            else:
                response = self.client.post(
                    f"/api/material-search/folders/{folder_id}/assets",
                    headers=self.headers,
                    files={"file": (name, raw)},
                    data={"relative_path": f"sample-folder/{name}"},
                )
            self.assertEqual(response.status_code, 200, response.text)

        finalized = self.client.post(
            f"/api/material-search/folders/{folder_id}/finalize", headers=self.headers
        )
        self.assertEqual(finalized.status_code, 200, finalized.text)
        folder = finalized.json()
        self.assertEqual(folder["status"], "READY")
        self.assertEqual(len(folder["assets"]), 3)
        self.assertNotIn("_blob_ref", folder["assets"][0])
        image_asset = next(asset for asset in folder["assets"] if asset["kind"] == "image")
        self.assertEqual(image_asset["metadata"]["feature_version"], "material-search-v3-perspective-sift")
        self.assertNotIn("local_features", image_asset["metadata"])
        self.assertNotIn("image_fingerprint", image_asset["metadata"])
        self.assertEqual(self.client.get(folder["assets"][0]["preview_url"]).status_code, 401)

        for asset in folder["assets"]:
            self.assertEqual(self.client.get(asset["preview_url"], headers=self.headers).status_code, 200)
            self.assertEqual(self.client.get(asset["blob_url"], headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get(folder["zip_url"], headers=self.headers).status_code, 200)

        for name, raw in self.fixtures().items():
            response = self.client.post(
                "/api/material-search/search", headers=self.headers, files={"file": (name, raw)}
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            result = payload["results"][0]
            self.assertEqual(result["folder_id"], folder_id)
            self.assertGreater(result["score"], 99)
            if name == "preview.png":
                self.assertEqual(payload["algorithm"], "cosine-then-sift-ransac")
                self.assertEqual(result["match_label"], "全局相似")
                self.assertEqual(payload["geometric_best"]["match_label"], "同图")
                self.assertEqual(payload["geometric_best"]["matched_asset_id"], result["matched_asset_id"])
                self.assertEqual(payload["geometric_best"]["first_search_rank"], 1)
            else:
                self.assertIsNone(payload["geometric_best"])

        different = Image.new("RGB", (240, 160), "white")
        different_draw = ImageDraw.Draw(different)
        different_draw.polygon([(25, 135), (120, 20), (215, 135)], outline="black", width=8)
        different_raw = io.BytesIO()
        different.save(different_raw, "PNG")
        different_search = self.client.post(
            "/api/material-search/search",
            headers=self.headers,
            files={"file": ("different.png", different_raw.getvalue())},
        )
        self.assertEqual(different_search.status_code, 200, different_search.text)
        different_payload = different_search.json()
        different_result = different_payload["geometric_best"]
        self.assertLess(different_result["score"], 50)
        self.assertEqual(different_result["match_label"], "未验证")

        with _connection() as connection:
            connection.execute(
                "UPDATE material_assets SET metadata_json = ? WHERE id = ?",
                ("{}", image_asset["id"]),
            )
            connection.commit()
        reindexed = self.client.post("/api/material-search/reindex", headers=self.headers)
        self.assertEqual(reindexed.status_code, 200, reindexed.text)
        self.assertEqual(reindexed.json()["updated"], 1)
        self.assertEqual(reindexed.json()["failed"], 0)
        reindex_again = self.client.post("/api/material-search/reindex", headers=self.headers)
        self.assertEqual(reindex_again.status_code, 200, reindex_again.text)
        self.assertEqual(reindex_again.json()["skipped"], 1)

        query_name = "preview.png"
        query_raw = self.fixtures()[query_name]
        query_upload_id = "b" * 32
        query_midpoint = len(query_raw) // 2
        for chunk_index, chunk in enumerate((query_raw[:query_midpoint], query_raw[query_midpoint:])):
            chunk_response = self.client.post(
                f"/api/material-search/search/uploads/{query_upload_id}/chunks/{chunk_index}",
                headers=self.headers,
                files={"chunk": (f"{query_name}.part", chunk)},
            )
            self.assertEqual(chunk_response.status_code, 200, chunk_response.text)
        chunked_search = self.client.post(
            f"/api/material-search/search/uploads/{query_upload_id}/complete",
            headers=self.headers,
            json={"filename": query_name, "relative_path": query_name, "chunk_count": 2},
        )
        self.assertEqual(chunked_search.status_code, 200, chunked_search.text)
        self.assertEqual(chunked_search.json()["results"][0]["folder_id"], folder_id)

        forbidden = self.client.delete(f"/api/material-search/folders/{folder_id}")
        self.assertEqual(forbidden.status_code, 401)
        deleted = self.client.delete(
            f"/api/material-search/folders/{folder_id}", headers=self.headers
        )
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(
            self.client.get("/api/material-search/folders", headers=self.headers).json()["folders"], []
        )
        self.assertEqual(
            self.client.get(f"/api/material-search/folders/{folder_id}", headers=self.headers).status_code,
            404,
        )
        trash = self.client.get("/api/material-search/trash", headers=self.headers)
        self.assertEqual(trash.status_code, 200, trash.text)
        self.assertEqual(trash.json()["folders"][0]["id"], folder_id)
        self.assertEqual(trash.json()["retention_days"], 30)

        hidden_search = self.client.post(
            "/api/material-search/search",
            headers=self.headers,
            files={"file": ("preview.png", self.fixtures()["preview.png"])},
        )
        self.assertEqual(hidden_search.status_code, 200, hidden_search.text)
        self.assertEqual(hidden_search.json()["results"], [])

        restored = self.client.post(
            f"/api/material-search/trash/{folder_id}/restore", headers=self.headers
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["id"], folder_id)
        self.assertEqual(
            self.client.get("/api/material-search/trash", headers=self.headers).json()["folders"], []
        )

        self.assertEqual(
            self.client.delete(f"/api/material-search/folders/{folder_id}", headers=self.headers).status_code,
            204,
        )
        permanently_deleted = self.client.delete(
            f"/api/material-search/trash/{folder_id}", headers=self.headers
        )
        self.assertEqual(permanently_deleted.status_code, 204, permanently_deleted.text)
        self.assertEqual(
            self.client.get("/api/material-search/trash", headers=self.headers).json()["folders"], []
        )
        self.assertEqual(
            self.client.post(f"/api/material-search/trash/{folder_id}/restore", headers=self.headers).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
