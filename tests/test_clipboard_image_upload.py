from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "frontend_dist" / "assets" / "paste-upload.js"
PAGES_WITH_IMAGE_UPLOADS = (
    ROOT / "frontend_dist" / "index.html",
    ROOT / "frontend_dist" / "upload.html",
    ROOT / "image3d_frontend" / "index.html",
    ROOT / "text2image_frontend" / "index.html",
    ROOT / "similarity_search_frontend" / "index.html",
    ROOT / "photography_frontend" / "index.html",
)


class ClipboardImageUploadTests(unittest.TestCase):
    def test_shared_script_is_loaded_by_every_image_upload_page(self):
        for page in PAGES_WITH_IMAGE_UPLOADS:
            with self.subTest(page=page.relative_to(ROOT)):
                self.assertIn("/assets/paste-upload.js", page.read_text(encoding="utf-8"))

    def test_script_converts_clipboard_images_into_normal_file_input_changes(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("event.clipboardData", source)
        self.assertIn("new DataTransfer()", source)
        self.assertIn('new File([file], `clipboard-', source)
        self.assertIn('new Event("change", { bubbles: true })', source)

    def test_script_does_not_hijack_normal_text_paste(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("isEditingText()", source)
        self.assertIn("HTMLTextAreaElement", source)
        self.assertIn("contenteditable", source)

    def test_ambiguous_pages_require_an_explicit_upload_target(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("inputs.length === 1 ? inputs[0] : null", source)
        self.assertIn("请先点击或将鼠标移到要上传图片的区域", source)

    def test_shared_script_routes_file_drops_through_normal_input_changes(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn('document.addEventListener("dragover"', source)
        self.assertIn('document.addEventListener("drop"', source)
        self.assertIn("event.dataTransfer", source)
        self.assertIn("assignDroppedFiles(input, files)", source)
        self.assertIn('input.dispatchEvent(new Event("change", { bubbles: true }))', source)

    def test_drop_supports_file_types_and_dragged_directories(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("function acceptsFile(input, file)", source)
        self.assertIn('input.hasAttribute("webkitdirectory")', source)
        self.assertIn("webkitGetAsEntry", source)
        self.assertIn('Object.defineProperty(file, "webkitRelativePath"', source)

    def test_drop_gives_a_visible_target_and_does_not_open_files_in_browser(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn('dragAnchor.dataset.fileDropActive = "true"', source)
        self.assertIn("event.preventDefault()", source)
        self.assertIn("请将文件拖到具体的上传区域", source)


if __name__ == "__main__":
    unittest.main()
