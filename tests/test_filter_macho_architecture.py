import json
import sys
from pathlib import Path
import pytest

from scripts import filter_macho_architecture

def test_pruning_removes_opencv_mismatched_binaries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    report = tmp_path / "report.json"
    
    # We mock macho_arches to return specific sets
    def fake_macho_arches(header: bytes):
        if b"x86_only" in header:
            return {"x86_64"}
        if b"arm_only" in header:
            return {"arm64"}
        if b"fat" in header:
            return {"x86_64", "arm64"}
        return None
        
    monkeypatch.setattr(filter_macho_architecture, "macho_arches", fake_macho_arches)
    
    # Create fake files
    f1 = tmp_path / "cv2" / ".dylibs" / "x86.dylib"
    f1.parent.mkdir(parents=True)
    f1.write_bytes(b"x86_only")
    
    f2 = tmp_path / "cv2" / ".dylibs" / "arm.dylib"
    f2.write_bytes(b"arm_only")
    
    f3 = tmp_path / "cv2" / ".dylibs" / "fat.dylib"
    f3.write_bytes(b"fat")
    
    binaries = [
        ("cv2/x86.dylib", str(f1), "EXTENSION"),
        ("cv2/arm.dylib", str(f2), "EXTENSION"),
        ("cv2/fat.dylib", str(f3), "EXTENSION"),
    ]
    
    filtered = filter_macho_architecture.filter_binaries(binaries, "arm64", report)
    
    assert len(filtered) == 2
    assert filtered[0][0] == "cv2/arm.dylib"
    assert filtered[1][0] == "cv2/fat.dylib"
    
    assert report.exists()
    removed = json.loads(report.read_text())
    assert len(removed) == 1
    assert removed[0]["dest"] == "cv2/x86.dylib"
    assert removed[0]["architectures"] == ["x86_64"]
    assert "digest" in removed[0]

def test_pruning_raises_error_for_non_opencv_mismatched_binaries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    report = tmp_path / "report.json"
    
    def fake_macho_arches(header: bytes):
        return {"x86_64"}
        
    monkeypatch.setattr(filter_macho_architecture, "macho_arches", fake_macho_arches)
    
    f1 = tmp_path / "other" / "x86.dylib"
    f1.parent.mkdir(parents=True)
    f1.write_bytes(b"x86_only")
    
    binaries = [
        ("other/x86.dylib", str(f1), "EXTENSION"),
    ]
    
    with pytest.raises(ValueError, match="Target-incompatible binary found outside of OpenCV scope"):
        filter_macho_architecture.filter_binaries(binaries, "arm64", report)

def test_pruning_raises_error_on_macho_parsing_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    report = tmp_path / "report.json"
    
    def fake_macho_arches(header: bytes):
        raise ValueError("Invalid Mach-O header")
        
    monkeypatch.setattr(filter_macho_architecture, "macho_arches", fake_macho_arches)
    
    f1 = tmp_path / "cv2" / "broken.dylib"
    f1.parent.mkdir(parents=True)
    f1.write_bytes(b"broken")
    
    binaries = [
        ("cv2/broken.dylib", str(f1), "EXTENSION"),
    ]
    
    with pytest.raises(ValueError, match="Failed to inspect Mach-O architectures"):
        filter_macho_architecture.filter_binaries(binaries, "arm64", report)

