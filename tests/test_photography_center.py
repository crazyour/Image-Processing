from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import app as app_module


DATABASE_ENVIRONMENT = (
    "NEON_DATABASE_URL", "POSTGRES_URL", "DATABASE_URL", "DATABASE_URL_POOLED",
    "NEON_PROJECT_ID", "BLOB_READ_WRITE_TOKEN",
)


class PhotographyCenterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        names = (
            "LOCAL_DATA_DIR", "ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_NAME",
            *DATABASE_ENVIRONMENT,
        )
        self.saved_environment = {name: os.environ.get(name) for name in names}
        for name in DATABASE_ENVIRONMENT:
            os.environ.pop(name, None)
        os.environ.update({
            "LOCAL_DATA_DIR": self.tempdir.name,
            "ADMIN_USERNAME": "photo-owner",
            "ADMIN_PASSWORD": "photo-owner-password",
            "ADMIN_NAME": "摄影管理员",
        })
        self.admin = TestClient(app_module.app)
        response = self.admin.post("/api/auth/login", json={
            "username": "photo-owner", "password": "photo-owner-password",
        })
        self.assertEqual(response.status_code, 200)
        created = self.admin.post("/api/admin/users", json={
            "username": "photographer", "name": "摄影员",
            "password": "photographer-password", "role": "member", "features": [],
        })
        self.member_record = created.json()["user"]
        self.member = TestClient(app_module.app)
        logged_in = self.member.post("/api/auth/login", json={
            "username": "photographer", "password": "photographer-password",
        })
        self.assertEqual(logged_in.status_code, 200)

    def tearDown(self):
        self.member.close()
        self.admin.close()
        for name, value in self.saved_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    @staticmethod
    def jpeg(width: int = 3200, height: int = 1800) -> bytes:
        image = Image.new("RGB", (width, height), "#8dbca7")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=98)
        return output.getvalue()

    def create_folder(self, with_member: bool = True) -> dict:
        response = self.admin.post("/api/admin/photography/folders", json={
            "name": "2026 秋季拍摄",
            "member_ids": [self.member_record["id"]] if with_member else [],
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["folder"]

    def test_page_is_available_but_only_assigned_folders_are_listed(self):
        folder = self.create_folder(with_member=False)
        page = self.member.get("/photography-center")
        self.assertEqual(page.status_code, 200)
        self.assertIn("摄影中心站使用说明", page.text)
        self.assertIn("不等于上传前的逐像素原件", page.text)
        self.assertIn('id="help-open"', page.text)
        self.assertEqual(self.member.get("/api/photography/folders").json()["folders"], [])
        denied = self.member.get(f"/api/photography/folders/{folder['id']}/media")
        self.assertEqual(denied.status_code, 403)

        granted = self.admin.put(f"/api/admin/photography/folders/{folder['id']}", json={
            "name": folder["name"], "member_ids": [self.member_record["id"]],
        })
        self.assertEqual(granted.status_code, 200)
        folders = self.member.get("/api/photography/folders").json()["folders"]
        self.assertEqual([item["id"] for item in folders], [folder["id"]])

    def test_home_places_photography_center_in_craft_tools(self):
        home = self.member.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn('class="tool-card tone-photography" href="/photography-center"', home.text)
        self.assertNotIn("card-photography", home.text)

    def test_photography_page_uses_light_theme_without_savings_metrics(self):
        root = Path(__file__).resolve().parent.parent / "photography_frontend"
        css = (root / "styles.css").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        page = (root / "index.html").read_text(encoding="utf-8")
        self.assertIn("color-scheme: light", css)
        self.assertNotIn("saved-total", page)
        self.assertNotIn("saving_percent", script)
        self.assertNotIn("已节省", script)

    def test_member_uploads_only_compressed_image_and_download_restores_dimensions(self):
        folder = self.create_folder()
        original = self.jpeg()
        upload = self.member.post(
            f"/api/photography/folders/{folder['id']}/media",
            files={"file": ("camera-original.jpg", original, "image/jpeg")},
            data={"filename": "秋季产品主图"},
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        item = upload.json()["item"]
        self.assertEqual(item["name"], "秋季产品主图")
        self.assertEqual(item["media_type"], "image")
        self.assertEqual((item["original_width"], item["original_height"]), (3200, 1800))
        self.assertEqual((item["stored_width"], item["stored_height"]), (2560, 1440))
        self.assertLess(item["stored_size_bytes"], item["original_size_bytes"])

        blob_files = list((Path(self.tempdir.name) / "material-private" / "photography").rglob("*"))
        stored_files = [path for path in blob_files if path.is_file()]
        self.assertEqual(len(stored_files), 1)
        self.assertEqual(stored_files[0].suffix, ".webp")
        self.assertNotIn(original, [path.read_bytes() for path in stored_files])

        preview = self.member.get(item["content_url"])
        self.assertEqual(preview.status_code, 200)
        with Image.open(io.BytesIO(preview.content)) as image:
            self.assertEqual(image.size, (2560, 1440))

        download = self.member.get(item["download_url"])
        self.assertEqual(download.status_code, 200)
        with Image.open(io.BytesIO(download.content)) as image:
            self.assertEqual(image.size, (3200, 1800))
        self.assertIn("filename*=UTF-8", download.headers["content-disposition"])

    def test_unassigned_member_cannot_read_media_even_with_known_id(self):
        folder = self.create_folder(with_member=False)
        upload = self.admin.post(
            f"/api/photography/folders/{folder['id']}/media",
            files={"file": ("private.jpg", self.jpeg(640, 480), "image/jpeg")},
            data={"filename": "内部样片"},
        )
        self.assertEqual(upload.status_code, 201)
        item = upload.json()["item"]
        self.assertEqual(self.member.get(item["content_url"]).status_code, 403)
        self.assertEqual(self.member.get(item["download_url"]).status_code, 403)

    def test_chunked_upload_assembles_then_removes_original_staging_parts(self):
        folder = self.create_folder()
        original = self.jpeg(1600, 900)
        upload_id = "a" * 32
        midpoint = len(original) // 2
        for index, part in enumerate((original[:midpoint], original[midpoint:])):
            response = self.member.post(
                f"/api/photography/folders/{folder['id']}/uploads/{upload_id}/chunks/{index}",
                files={"chunk": ("photo.part", part, "application/octet-stream")},
            )
            self.assertEqual(response.status_code, 200, response.text)
        completed = self.member.post(
            f"/api/photography/folders/{folder['id']}/uploads/{upload_id}/complete",
            json={"filename": "chunked.jpg", "custom_filename": "分片上传", "chunk_count": 2},
        )
        self.assertEqual(completed.status_code, 201, completed.text)
        self.assertEqual(completed.json()["item"]["name"], "分片上传")
        staging = Path(self.tempdir.name) / "material-private" / "photography-staging"
        self.assertFalse(any(path.is_file() for path in staging.rglob("*") if staging.exists()))

    def test_non_empty_folder_cannot_be_deleted(self):
        folder = self.create_folder()
        self.admin.post(
            f"/api/photography/folders/{folder['id']}/media",
            files={"file": ("kept.jpg", self.jpeg(640, 480), "image/jpeg")},
            data={"filename": "保留"},
        )
        response = self.admin.delete(f"/api/admin/photography/folders/{folder['id']}")
        self.assertEqual(response.status_code, 409)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for the video integration test")
    def test_video_is_transcoded_and_downloaded_as_mp4(self):
        folder = self.create_folder()
        source = Path(self.tempdir.name) / "source.mov"
        subprocess.run([
            shutil.which("ffmpeg"), "-y", "-v", "error", "-f", "lavfi",
            "-i", "testsrc2=size=640x360:rate=12", "-t", "0.5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ], check=True)
        upload = self.member.post(
            f"/api/photography/folders/{folder['id']}/media",
            files={"file": ("behind-scenes.mov", source.read_bytes(), "video/quicktime")},
            data={"filename": "拍摄花絮"},
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        item = upload.json()["item"]
        self.assertEqual(item["media_type"], "video")
        self.assertEqual((item["original_width"], item["original_height"]), (640, 360))
        preview = self.member.get(item["content_url"])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"], "video/mp4")
        download = self.member.get(item["download_url"])
        self.assertEqual(download.status_code, 200)
        self.assertIn(".mp4", download.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
