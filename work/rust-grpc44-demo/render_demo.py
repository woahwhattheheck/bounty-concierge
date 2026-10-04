"""Render checked source and recorded CI results; this is not live footage."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
from PIL import Image, ImageDraw, ImageFont


def render(bundle: Path, output: Path) -> None:
    source = bundle / "source"
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["run_id"] == "37242196793"
    assert manifest["handoff_sha"] == "e5204b6aa8d4acc62999f54d443a6a1d3abc3fa3"
    for name, meta in manifest["files"].items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == meta["sha256"], name
    main = (source / "src/main.rs").read_text()
    build = (source / "build.rs").read_text()
    deps = (bundle / "dependencies.txt").read_text()
    for version in ["tonic v0.14.6", "opentelemetry v0.33.0", "shuttle-runtime v0.57.0"]:
        assert version in deps
    exporter = main[main.index("    let exporter ="):main.index("    let tracer =")].rstrip()
    serving = main[main.index("        TonicServer::builder()"):main.index("        Ok(())", main.index("        TonicServer::builder()"))].rstrip()
    scenes = [
        ("Current compatible dependencies", "Versions from the validated Cargo.lock", [
            "Tonic / tonic-prost / reflection     0.14.6",
            "Prost                              0.14.4",
            "OpenTelemetry / SDK / OTLP           0.33.0",
            "Shuttle runtime                     0.57.0",
            "Tracing OpenTelemetry               0.34.0",
            "Tonic tracing middleware            0.42.1", "",
            "One aligned telemetry stack; refreshed lockfile.",
            "News RPC implementations and protobuf schema retained.",
        ]),
        ("Protobuf generation and server transport", "Actual snippets from the checked source",
            build[build.index("    tonic_prost_build::configure()"):build.rfind("}")].rstrip().splitlines()
            + [""] + serving.splitlines()),
        ("Current OpenTelemetry APIs", "Actual exporter/provider migration", exporter.splitlines()),
        ("Recorded CI result: SUCCESS", "Recorded on 2026-10-04; not live terminal or deployed-service footage", [
            "cargo fmt --all -- --check", "  PASS", "",
            "cargo test --locked --all-features", "  PASS - existing repository test", "",
            "cargo clippy --locked --all-targets --all-features -- -D warnings", "  PASS", "",
            "Run 37242196793  /  job 111552975327",
            "Completed 2026-10-04T23:02:55Z",
            "No production deployment or paid telemetry calls were made.",
        ]),
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    frames = output.parent / "demo-frames"
    frames.mkdir(parents=True, exist_ok=True)
    root = Path("/usr/share/fonts/truetype/dejavu")
    titlefont = ImageFont.truetype(str(root / "DejaVuSans-Bold.ttf"), 34)
    subtitlefont = ImageFont.truetype(str(root / "DejaVuSans.ttf"), 20)
    codefont = ImageFont.truetype(str(root / "DejaVuSansMono.ttf"), 22)
    small = ImageFont.truetype(str(root / "DejaVuSans.ttf"), 16)
    transcript = []
    for i, (title, subtitle, lines) in enumerate(scenes):
        image = Image.new("RGB", (1280, 720), (14, 22, 36))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1280, 7), fill=(86, 192, 186))
        draw.text((48, 29), "rust-grpc / issue #44", font=small, fill=(164, 184, 205))
        draw.text((48, 72), title, font=titlefont, fill=(238, 244, 249))
        draw.text((48, 123), subtitle, font=subtitlefont, fill=(169, 190, 210))
        draw.rounded_rectangle((38, 163, 1242, 643), radius=16, fill=(23, 34, 51))
        y = 185
        for line in lines:
            assert draw.textlength(line, font=codefont) < 1140, line
            assert y < 624, (title, y)
            fill = (124, 224, 174) if line.strip().startswith("PASS") else (225, 235, 245)
            draw.text((61, y), line, font=codefont, fill=fill)
            y += 27
        draw.text((48, 674), "Source base e5faeec6e9d8  |  checked handoff e5204b6aa8d4", font=small, fill=(164, 184, 205))
        draw.text((1163, 674), f"{i + 1} / 4", font=small, fill=(164, 184, 205))
        image.save(frames / f"{i}.png")
        transcript.extend([title, subtitle, *lines, ""])
    concat = frames / "frames.txt"
    concat.write_text("".join(f"file '{i}.png'\nduration 10\n" for i in range(4)) + "file '3.png'\n")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-vf", "fps=10", "-t", "40", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(output),
    ], check=True)
    output.with_suffix(".txt").write_text("\n".join(transcript) + "\n")
    print(json.dumps({"path": str(output), "bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render(args.bundle, args.output)
