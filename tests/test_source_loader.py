import subprocess
from unittest.mock import MagicMock

import pytest

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


def test_load_pdf_source_reports_pdftotext_stderr(tmp_path, monkeypatch):
    path = tmp_path / "encrypted.pdf"
    path.write_bytes(b"%PDF-1.7")

    monkeypatch.setattr("spec.source_loader.shutil.which", lambda name: "pdftotext")

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(
            2,
            args[0],
            stderr=b"Command Line Error: Incorrect password",
        )

    monkeypatch.setattr("spec.source_loader.subprocess.run", fail)

    with pytest.raises(RuntimeError) as excinfo:
        load_source_material(str(path))

    message = str(excinfo.value)
    assert "退出码 2" in message
    assert "Incorrect password" in message
    assert "未损坏且未加密" in message


def test_load_pdf_source_reports_pdftotext_timeout(tmp_path, monkeypatch):
    path = tmp_path / "slow.pdf"
    path.write_bytes(b"%PDF-1.7")

    monkeypatch.setattr("spec.source_loader.shutil.which", lambda name: "pdftotext")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=60, stderr=b"partial stderr")

    monkeypatch.setattr("spec.source_loader.subprocess.run", timeout)

    with pytest.raises(RuntimeError) as excinfo:
        load_source_material(str(path))

    message = str(excinfo.value)
    assert "执行超时" in message
    assert "超过 60 秒" in message
    assert "partial stderr" in message


def test_load_pdf_source_reports_pdftotext_os_error(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.7")

    monkeypatch.setattr("spec.source_loader.shutil.which", lambda name: "pdftotext")

    def fail_to_start(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("spec.source_loader.subprocess.run", fail_to_start)

    with pytest.raises(RuntimeError) as excinfo:
        load_source_material(str(path))

    message = str(excinfo.value)
    assert "启动失败" in message
    assert "permission denied" in message
