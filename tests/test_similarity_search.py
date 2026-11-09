from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
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
from vercel_app.similarity_engine import (
    FEATURE_VERSION,
    cosine_similarity,
    image_geometric_similarity,
    process_image,
)
from vercel_app.similarity_store import _connection, _ensure_schema
from vercel_app.interactive_segmentation import (
    postprocess_mask,
    preprocess_image,
)


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
        matching = image_geometric_similarity(query_metadata, reference_metadata)
        non_matching = image_geometric_similarity(query_metadata, distractor_metadata)
        self.assertGreaterEqual(matching["inliers"], 8)
        self.assertGreater(matching["score"], non_matching["score"] + 0.25)
        self.assertIn(matching["label"], {"可能同款", "高度可信"})

    def test_image_search_runs_geometry_only_for_fused_top_ten(self) -> None:
        candidates = [
            {
                "folder_id": f"folder-{index}",
                "folder_name": f"folder-{index}",
                "asset_id": f"asset-{index}",
                "original_name": f"image-{index}.png",
                "preview_url": f"preview-{index}",
                "vector": [1.0, index / 10.0],
                "metadata": {
                    "feature_version": FEATURE_VERSION,
                    "semantic_vector": [1.0, max(0.0, 1.0 - index / 20.0)],
                },
            }
            for index in range(12)
        ]

        def geometric(_query, _candidate):
            return {
                "score": 0.8,
                "label": "可能同款",
                "inliers": 8,
                "inlier_ratio": 0.4,
                "coverage": 0.1,
                "method": "sift-ransac",
            }

        folder = {"previews": [], "assets": [], "zip_url": None}
        with (
            patch("vercel_app.similarity_api.process_file", return_value=(
                "image", b"preview", [1.0, 0.0], {"semantic_vector": [1.0, 1.0]},
            )),
            patch("vercel_app.similarity_api.searchable_assets", return_value=candidates),
            patch("vercel_app.similarity_api.get_folder", return_value=folder),
            patch("vercel_app.similarity_api.image_geometric_similarity", side_effect=geometric) as verify,
        ):
            payload = _search_materials("library", "query.png", b"query")

        self.assertEqual(len(payload["results"]), 10)
        self.assertEqual(verify.call_count, 10)
        self.assertEqual(payload["results"][0]["matched_asset_id"], "asset-0")
        self.assertEqual(payload["geometric_best"]["matched_asset_id"], "asset-0")
        self.assertEqual(payload["algorithm"], "semantic-shape-fusion-then-sift-ransac")
        self.assertEqual(payload["fusion"]["semantic_weight"], 0.45)
        self.assertEqual(payload["fusion"]["shape_weight"], 0.55)

    def test_semantic_and_shape_rank_fusion_can_reject_a_wrong_category(self) -> None:
        candidates = [
            {
                "folder_id": "wrong", "folder_name": "wrong", "asset_id": "wrong-shape",
                "original_name": "sun.png", "preview_url": "wrong", "vector": [1.0, 0.0],
                "metadata": {"feature_version": FEATURE_VERSION, "semantic_vector": [0.0, 1.0]},
            },
            {
                "folder_id": "correct", "folder_name": "correct", "asset_id": "correct-category",
                "original_name": "chicken.png", "preview_url": "correct", "vector": [0.8, 0.6],
                "metadata": {"feature_version": FEATURE_VERSION, "semantic_vector": [1.0, 0.0]},
            },
            {
                "folder_id": "other", "folder_name": "other", "asset_id": "other",
                "original_name": "other.png", "preview_url": "other", "vector": [0.0, 1.0],
                "metadata": {"feature_version": FEATURE_VERSION, "semantic_vector": [0.7, 0.7]},
            },
        ]
        folder = {"previews": [], "assets": [], "zip_url": None}
        with (
            patch("vercel_app.similarity_api.process_file", return_value=(
                "image", b"preview", [1.0, 0.0], {"semantic_vector": [1.0, 0.0]},
            )),
            patch("vercel_app.similarity_api.searchable_assets", return_value=candidates),
            patch("vercel_app.similarity_api.get_folder", return_value=folder),
            patch("vercel_app.similarity_api.image_geometric_similarity", return_value={
                "score": 0.0, "label": "未验证", "inliers": 0,
                "inlier_ratio": 0.0, "coverage": 0.0, "method": "sift-ransac",
            }),
        ):
            payload = _search_materials("library", "query.png", b"query")

        self.assertEqual(payload["results"][0]["matched_asset_id"], "correct-category")
        self.assertGreater(payload["results"][0]["semantic_score"], payload["results"][1]["semantic_score"])

    def test_second_search_is_shown_above_first_with_separate_labels(self) -> None:
        root = Path(__file__).resolve().parent.parent / "similarity_search_frontend"
        page = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")

        self.assertLess(page.index('id="second-search"'), page.index('id="first-search-title"'))
        self.assertIn('stage: imagePipeline ? "global" : "feature"', script)
        self.assertIn('resultCard(result.geometric_best, { stage: "geometric" })', script)
        self.assertIn("语义45%＋轮廓55%", script)
        self.assertIn("几何相似度", script)
        self.assertNotIn("几何验证最佳", script)
        self.assertNotIn("第一次搜索排名", script)
        self.assertIn('"X-Chunk-SHA256": await sha256Hex(chunk)', script)
        self.assertIn("file_size: file.size", script)
        self.assertIn("failures.push(error)", script)
        self.assertIn("本次上传未保存", script)

    def test_chunk_integrity_and_complete_size_are_validated(self) -> None:
        raw = self.fixtures()["preview.png"]
        invalid_hash = self.client.post(
            f"/api/material-search/search/uploads/{'c' * 32}/chunks/0",
            headers={
                **self.headers,
                "X-Chunk-Size": str(len(raw)),
                "X-Chunk-SHA256": "0" * 64,
            },
            files={"chunk": ("preview.png.part", raw)},
        )
        self.assertEqual(invalid_hash.status_code, 422, invalid_hash.text)
        self.assertEqual(invalid_hash.json()["message"], "上传分片内容校验失败，请重新上传")

        upload_id = "d" * 32
        uploaded = self.client.post(
            f"/api/material-search/search/uploads/{upload_id}/chunks/0",
            headers={
                **self.headers,
                "X-Chunk-Size": str(len(raw)),
                "X-Chunk-SHA256": hashlib.sha256(raw).hexdigest(),
            },
            files={"chunk": ("preview.png.part", raw)},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        invalid_size = self.client.post(
            f"/api/material-search/search/uploads/{upload_id}/complete",
            headers=self.headers,
            json={
                "filename": "preview.png",
                "relative_path": "preview.png",
                "chunk_count": 1,
                "file_size": len(raw) + 1,
            },
        )
        self.assertEqual(invalid_size.status_code, 422, invalid_size.text)
        self.assertIn("文件分片合并后大小不一致", invalid_size.json()["message"])

    def test_interactive_segmentation_endpoints_keep_the_model_pipeline_isolated(self) -> None:
        raw = self.fixtures()["preview.png"]
        session_id = "e" * 32
        mask_stream = io.BytesIO()
        Image.new("L", (240, 160), 255).save(mask_stream, "PNG")
        with patch(
            "vercel_app.similarity_api.create_segmentation",
            return_value={
                "session_id": session_id,
                "width": 240,
                "height": 160,
                "model": "EdgeSAM-3x ONNX",
                "encoding_ms": 18,
                "expires_in": 900,
            },
        ) as create:
            initialised = self.client.post(
                "/api/material-search/segmentations",
                headers=self.headers,
                files={"image": ("query.png", raw)},
            )
        self.assertEqual(initialised.status_code, 200, initialised.text)
        self.assertEqual(initialised.json()["session_id"], session_id)
        create.assert_called_once_with(self.headers["X-Material-Library"], raw)

        with patch(
            "vercel_app.similarity_api.predict_segmentation",
            return_value=(mask_stream.getvalue(), 0.875),
        ) as predict:
            predicted = self.client.post(
                f"/api/material-search/segmentations/{session_id}/mask",
                headers=self.headers,
                json={"points": [{"x": 0.5, "y": 0.45, "label": 1}]},
            )
        self.assertEqual(predicted.status_code, 200, predicted.text)
        self.assertEqual(predicted.headers["content-type"], "image/png")
        self.assertEqual(predicted.headers["x-segmentation-score"], "0.875000")
        predict.assert_called_once()

        replacement_session_id = "f" * 32
        with patch(
            "vercel_app.similarity_api.create_segmentation",
            return_value={
                "session_id": replacement_session_id,
                "width": 240,
                "height": 160,
                "model": "EdgeSAM-3x ONNX",
                "encoding_ms": 20,
                "expires_in": 900,
            },
        ) as recreate, patch(
            "vercel_app.similarity_api.predict_segmentation",
            return_value=(mask_stream.getvalue(), 0.91),
        ) as stateless_predict:
            recovered = self.client.post(
                "/api/material-search/segmentations/mask",
                headers=self.headers,
                files={"image": ("query.png", raw)},
                data={"points": json.dumps([{"x": 0.5, "y": 0.45, "label": 1}])},
            )
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.headers["x-segmentation-session"], replacement_session_id)
        self.assertEqual(recovered.headers["x-segmentation-score"], "0.910000")
        recreate.assert_called_once_with(self.headers["X-Material-Library"], raw)
        stateless_predict.assert_called_once()

        invalid = self.client.post(
            f"/api/material-search/segmentations/{session_id}/mask",
            headers=self.headers,
            json={"points": [{"x": 1.2, "y": 0.5, "label": 1}]},
        )
        self.assertEqual(invalid.status_code, 422)
        with patch("vercel_app.similarity_api.delete_segmentation", return_value=True) as delete:
            discarded = self.client.delete(
                f"/api/material-search/segmentations/{session_id}", headers=self.headers
            )
        self.assertEqual(discarded.status_code, 204)
        delete.assert_called_once_with(self.headers["X-Material-Library"], session_id)

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
                        headers={
                            **self.headers,
                            "X-Chunk-Size": str(len(chunk)),
                            "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                        },
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
                        "file_size": len(raw),
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
        self.assertEqual(image_asset["metadata"]["feature_version"], FEATURE_VERSION)
        self.assertNotIn("local_features", image_asset["metadata"])
        self.assertNotIn("image_fingerprint", image_asset["metadata"])
        self.assertNotIn("semantic_vector", image_asset["metadata"])
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
                self.assertEqual(payload["algorithm"], "semantic-shape-fusion-then-sift-ransac")
                self.assertEqual(result["match_label"], "语义与主体形状融合")
                self.assertEqual(payload["geometric_best"]["match_label"], "同图")
                self.assertEqual(payload["geometric_best"]["matched_asset_id"], result["matched_asset_id"])
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

        plan_response = self.client.get("/api/material-search/reindex/plan", headers=self.headers)
        self.assertEqual(plan_response.status_code, 200, plan_response.text)
        plan = plan_response.json()
        self.assertEqual(plan["feature_version"], FEATURE_VERSION)
        self.assertEqual(plan["total"], 1)
        self.assertEqual(plan["items"][0]["asset_id"], image_asset["id"])

        forced_reindex = self.client.post(
            f"/api/material-search/reindex/assets/{image_asset['id']}", headers=self.headers,
        )
        self.assertEqual(forced_reindex.status_code, 200, forced_reindex.text)
        self.assertEqual(forced_reindex.json()["feature_version"], FEATURE_VERSION)
        self.assertEqual(forced_reindex.json()["semantic_dimension"], 1000)
        self.assertGreaterEqual(forced_reindex.json()["local_feature_count"], 0)

        batch_reindex = self.client.post(
            "/api/material-search/reindex/batch",
            headers=self.headers,
            json={"asset_ids": [image_asset["id"], "missing-image"]},
        )
        self.assertEqual(batch_reindex.status_code, 200, batch_reindex.text)
        self.assertEqual(batch_reindex.json()["updated"], 1)
        self.assertEqual(batch_reindex.json()["failed"], 1)

        query_name = "preview.png"
        query_raw = self.fixtures()[query_name]
        query_upload_id = "b" * 32
        query_midpoint = len(query_raw) // 2
        for chunk_index, chunk in enumerate((query_raw[:query_midpoint], query_raw[query_midpoint:])):
            chunk_response = self.client.post(
                f"/api/material-search/search/uploads/{query_upload_id}/chunks/{chunk_index}",
                headers={
                    **self.headers,
                    "X-Chunk-Size": str(len(chunk)),
                    "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                },
                files={"chunk": (f"{query_name}.part", chunk)},
            )
            self.assertEqual(chunk_response.status_code, 200, chunk_response.text)
        chunked_search = self.client.post(
            f"/api/material-search/search/uploads/{query_upload_id}/complete",
            headers=self.headers,
            json={
                "filename": query_name,
                "relative_path": query_name,
                "chunk_count": 2,
                "file_size": len(query_raw),
            },
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


class SimilaritySearchSubjectSelectionFrontendTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parent.parent / "similarity_search_frontend"
        self.page = (root / "index.html").read_text(encoding="utf-8")
        self.script = (root / "app.js").read_text(encoding="utf-8")
        self.styles = (root / "styles.css").read_text(encoding="utf-8")

    def test_freehand_lasso_is_completely_removed(self) -> None:
        combined = self.page + self.script + self.styles
        self.assertNotIn("start-lasso", combined)
        self.assertNotIn("redraw-subject", combined)
        self.assertNotIn("自由套索", combined)
        self.assertNotIn("subjectDraft", combined)
        self.assertNotIn("finishSubjectLasso", combined)
        for control_id in (
            "undo-subject",
            "reset-subject",
            "preview-subject",
            "confirm-subject-search",
        ):
            self.assertIn(f'id="{control_id}"', self.page)

    def test_auto_selection_uses_normalised_points_and_an_original_size_binary_mask(self) -> None:
        self.assertIn("x: point.x / canvas.width", self.script)
        self.assertIn("y: point.y / canvas.height", self.script)
        self.assertIn("full.width = image.naturalWidth", self.script)
        self.assertIn("full.height = image.naturalHeight", self.script)
        self.assertIn("value = fullPixels.data[offset] >= 128 ? 255 : 0", self.script)
        self.assertIn("blob: binaryBlob", self.script)

    def test_image_search_supports_original_crop_and_mask_without_forcing_a_mask(self) -> None:
        self.assertIn('data-query-method="original"', self.page)
        self.assertIn('data-query-method="crop"', self.page)
        self.assertIn('data-query-method="mask"', self.page)
        self.assertNotIn('kindOf(state.queryFile.name) === "image" && !state.queryMask', self.script)
        self.assertNotIn("window.alert(prompt)", self.script)
        self.assertIn('if (state.queryMethod === "original") return file', self.script)
        self.assertIn('`${baseName}-crop.png`', self.script)
        self.assertIn('context.fillStyle = "#fff"', self.script)
        self.assertIn('context.globalCompositeOperation = "destination-in"', self.script)
        self.assertIn("context.drawImage(alphaMask, 0, 0)", self.script)
        self.assertIn('`${baseName}-subject.png`', self.script)
        self.assertIn('"semantic-shape-fusion-then-sift-ransac"', self.script)

    def test_cpu_auto_selection_controls_and_point_prompts_are_present(self) -> None:
        self.assertIn('data-query-method="mask"', self.page)
        self.assertIn('data-point-label="1"', self.page)
        self.assertIn('data-point-label="0"', self.page)
        self.assertIn('api("/api/material-search/segmentations"', self.script)
        self.assertIn('/mask`', self.script)
        self.assertIn("state.segmentationPoints.push", self.script)
        self.assertIn("event.button === 2 ? 0", self.script)
        self.assertIn('api("/api/material-search/segmentations/mask"', self.script)
        self.assertIn("error.status !== 410", self.script)
        self.assertNotIn("智能分割会话已过期", self.script)

    def test_image_upload_defaults_to_original_and_does_not_start_the_model(self) -> None:
        setup_start = self.script.index("async function setupSubjectEditor")
        setup_end = self.script.index("function resetSubjectSelection", setup_start)
        setup = self.script[setup_start:setup_end]
        self.assertIn('state.queryMethod = "original"', setup)
        self.assertNotIn("await startAutoSelection();", setup)

    def test_crop_mode_maps_the_canvas_selection_back_to_original_pixels(self) -> None:
        self.assertIn("crop.left * image.naturalWidth", self.script)
        self.assertIn("crop.top * image.naturalHeight", self.script)
        self.assertIn("sourceWidth", self.script)
        self.assertIn("sourceHeight", self.script)


class SubjectShapeFeatureTest(unittest.TestCase):
    @staticmethod
    def polygon_image(
        points: list[tuple[int, int]],
        *,
        color: str = "black",
        outline_only: bool = False,
        mode: str = "RGB",
    ) -> bytes:
        background = (255, 255, 255, 0) if mode == "RGBA" else "white"
        image = Image.new(mode, (320, 240), background)
        draw = ImageDraw.Draw(image)
        if outline_only:
            draw.polygon(points, outline=color, width=8)
        else:
            draw.polygon(points, fill=color)
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()

    def test_colour_does_not_change_the_subject_shape_vector(self) -> None:
        points = [(60, 180), (95, 55), (180, 35), (260, 115), (225, 195), (130, 210)]
        _, red_vector, _ = process_image(self.polygon_image(points, color="red"))
        _, black_vector, _ = process_image(self.polygon_image(points, color="black"))
        self.assertGreater(cosine_similarity(red_vector, black_vector), 0.999)

    def test_image_feature_source_contains_no_hsv_histogram_or_canny(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent / "vercel_app" / "similarity_engine.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("cv2.calcHist", source)
        self.assertNotIn("cv2.Canny", source)
        self.assertNotIn("cv2.COLOR_RGB2HSV", source)

    def test_closed_outline_and_filled_subject_compare_by_outer_shape(self) -> None:
        points = [(55, 180), (90, 50), (190, 35), (270, 125), (215, 205), (120, 210)]
        _, filled_vector, _ = process_image(self.polygon_image(points))
        _, outline_vector, _ = process_image(self.polygon_image(points, outline_only=True))
        self.assertGreater(cosine_similarity(filled_vector, outline_vector), 0.96)

    def test_scale_and_position_are_normalised_but_different_shapes_stay_apart(self) -> None:
        small = [(35, 175), (65, 65), (135, 45), (195, 115), (155, 190), (80, 205)]
        large = [(80, 190), (120, 45), (215, 20), (295, 110), (245, 210), (145, 230)]
        rectangle = [(90, 35), (230, 35), (230, 215), (90, 215)]
        _, small_vector, _ = process_image(self.polygon_image(small))
        _, large_vector, _ = process_image(self.polygon_image(large))
        _, rectangle_vector, _ = process_image(self.polygon_image(rectangle))
        same_shape = cosine_similarity(small_vector, large_vector)
        different_shape = cosine_similarity(small_vector, rectangle_vector)
        self.assertGreater(same_shape, 0.97)
        self.assertGreater(same_shape, different_shape + 0.15)

    def test_transparent_query_uses_its_alpha_channel_as_the_subject_mask(self) -> None:
        points = [(60, 190), (95, 45), (200, 35), (270, 135), (220, 210), (120, 215)]
        _, _, metadata = process_image(self.polygon_image(points, color="blue", mode="RGBA"))
        self.assertEqual(metadata["subject_mask_source"], "alpha")
        self.assertEqual(metadata["shape_feature_schema"], "subject-mask-contour-bbox-v2")
        self.assertGreater(metadata["subject_bbox_aspect"], 1.0)

    def test_image_index_stores_a_versioned_cpu_semantic_vector(self) -> None:
        points = [(60, 190), (95, 45), (200, 35), (270, 135), (220, 210), (120, 215)]
        _, _, metadata = process_image(self.polygon_image(points, color="orange"))
        self.assertEqual(metadata["feature_version"], "material-search-v6-semantic-shape")
        self.assertEqual(metadata["semantic_feature_schema"], "mobilenetv2-imagenet-logits-v1")
        self.assertEqual(metadata["semantic_model"], "MobileNetV2-12-QDQ")
        self.assertEqual(len(metadata["semantic_vector"]), 1000)
        self.assertAlmostEqual(float(np.linalg.norm(metadata["semantic_vector"])), 1.0, places=4)


class InteractiveSegmentationTransformTest(unittest.TestCase):
    def test_edgesam_keeps_onnxruntime_for_model_compatibility(self) -> None:
        requirements = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
        source = (
            Path(__file__).resolve().parent.parent
            / "vercel_app" / "interactive_segmentation.py"
        ).read_text()
        self.assertIn("onnxruntime", requirements)
        self.assertIn("ort.InferenceSession", source)
        self.assertNotIn("cv2.dnn.readNetFromONNX", source)

    def test_preprocess_matches_edgesam_1024_square_input(self) -> None:
        image = np.full((100, 200, 3), 127, dtype=np.uint8)
        tensor, input_size = preprocess_image(image)
        self.assertEqual(tensor.shape, (1, 3, 1024, 1024))
        self.assertEqual(input_size, (512, 1024))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertTrue(np.all(tensor[:, :, 512:, :] == 0))

    def test_postprocess_returns_an_original_size_binary_mask(self) -> None:
        logits = np.full((256, 256), -1.0, dtype=np.float32)
        logits[64:192, 64:192] = 1.0
        mask = postprocess_mask(logits, (512, 1024), (100, 200))
        self.assertEqual(mask.shape, (100, 200))
        self.assertEqual(mask.dtype, np.uint8)
        self.assertEqual(set(np.unique(mask)), {0, 255})


if __name__ == "__main__":
    unittest.main()
