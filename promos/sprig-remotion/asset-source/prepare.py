"""Exact original Sprig downsampling; called by scripts/rebuild-sprig.mjs."""
from pathlib import Path
import hashlib
import json
import sys
import PIL
from PIL import Image

if PIL.__version__ != "12.0.0":
    raise RuntimeError("Use Pillow==12.0.0 to retain the original downsampling implementation.")
source = Path(__file__).resolve().parent
data = json.loads(Path(sys.argv[1]).read_text())
output = Path(sys.argv[2])
output.mkdir(parents=True, exist_ok=True)
reference = json.loads((source / "reference-pixels.json").read_text())
records = []
for entry in data["records"]:
    clip, frame = entry["clip"], entry["frame"]
    image = Image.open(entry["master"]).convert("RGBA")
    image = image.resize((768, 768), Image.Resampling.LANCZOS)
    folder = output / clip
    folder.mkdir(exist_ok=True)
    target = folder / f"frame-{frame:04d}.png"
    image.save(target, compress_level=6)
    digest = hashlib.sha256(image.tobytes()).hexdigest()
    alpha = image.getchannel("A")
    edge_max = max(
        alpha.crop((0, 0, 768, 1)).getextrema()[1],
        alpha.crop((0, 767, 768, 768)).getextrema()[1],
        alpha.crop((0, 0, 1, 768)).getextrema()[1],
        alpha.crop((767, 0, 768, 768)).getextrema()[1],
    )
    records.append({"file": str(target.relative_to(output)), "frame": frame,
                    "pixelSHA256": digest, "edgeAlphaMax": edge_max,
                    "referencePixelsMatch": digest == reference["clips"][clip][str(frame)]})
report = {"width": 768, "height": 768, "fps": data["fps"],
          "alpha": "straight", "frames": len(records), "keyframesOnly": data["verifyOnly"],
          "sourceFingerprint": data["sourceFingerprint"],
          "pillow": PIL.__version__, "browser": data["browser"], "gl": data["gl"],
          "groundAnchor": data["groundAnchor"],
          "allReferencePixelsMatch": all(r["referencePixelsMatch"] for r in records),
          "allEdgesTransparent": all(r["edgeAlphaMax"] == 0 for r in records),
          "note": "Same-machine reference comparison. Cross-platform browser/GPU rasterization may differ; animation timing and camera are deterministic.",
          "files": records}
(output / "rebuild-report.json").write_text(json.dumps(report, indent=2)+"\n")
print(json.dumps({k: v for k, v in report.items() if k != "files"}), flush=True)
if not report["allEdgesTransparent"]:
    raise RuntimeError("Rendered content reached an image edge.")
if "--strict" in sys.argv and not report["allReferencePixelsMatch"]:
    raise RuntimeError("Strict decoded-pixel reference comparison failed; see rebuild-report.json.")
