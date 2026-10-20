from __future__ import annotations

import io
import os
import tempfile
import unittest

from PIL import Image, ImageDraw


os.environ["LOCAL_DATA_DIR"] = tempfile.mkdtemp(prefix="lingjie-material-test-")

from fastapi.testclient import TestClient
from app import app


class SimilaritySearchEndToEndTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
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
            result = response.json()["results"][0]
            self.assertEqual(result["folder_id"], folder_id)
            self.assertGreater(result["score"], 99)

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


if __name__ == "__main__":
    unittest.main()
