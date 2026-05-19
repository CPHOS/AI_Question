from unittest.mock import MagicMock

from spec.source_loader import load_source_material


def test_load_text_source(tmp_path):
    path = tmp_path / "idea.md"
    path.write_text("核心思路", encoding="utf-8")

    text = load_source_material(str(path))

    assert "【源文件】idea.md" in text
    assert "核心思路" in text


def test_load_pdf_source_with_page_markers(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.7")

    monkeypatch.setattr("spec.source_loader.shutil.which", lambda name: "pdftotext")
    completed = MagicMock()
    completed.stdout = "第一页\f第二页".encode("utf-8")
    monkeypatch.setattr("spec.source_loader.subprocess.run", lambda *args, **kwargs: completed)

    text = load_source_material(str(path))

    assert "【源文件】paper.pdf" in text
    assert "【PDF 第 1 页】" in text
    assert "【PDF 第 2 页】" in text
