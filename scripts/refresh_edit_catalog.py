#!/usr/bin/env python3
"""Generate the pinned editing catalog from upstream metadata, never model weights."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localsr.core.edit_memory import (  # noqa: E402
    GIB,
    activation_memory,
    estimate_edit_memory,
    reserve_for,
)

# Memory sizes Apple sells and common graphics-card sizes, in GiB.
MAC_TIERS = (16, 24, 32, 36, 48, 64, 96, 128)
GPU_TIERS = (8, 12, 16, 20, 24, 32, 48)


def unified_tier(model: dict) -> int:
    """Smallest Mac whose idle memory admits the bundle at 512 px.

    Roughly 70% of unified memory is free on an idle Mac, and the transformer
    with its working buffers must stay under 60% of it so Metal never wires
    more than Apple's recommended working set.
    """
    diffusion = next(f["size_bytes"] for f in model["files"] if f["role"] == "diffusion")
    vae = next((f["size_bytes"] for f in model["files"] if f["role"] == "vae"), 0)
    needed = estimate_edit_memory(model, True, 512)
    sampling = diffusion + vae + activation_memory(True, 512)
    for tier in MAC_TIERS:
        if needed + reserve_for(tier * GIB) <= 0.70 * tier * GIB and sampling <= 0.60 * tier * GIB:
            return tier
    raise ValueError(f"no Mac tier holds {model['model_id']}")


def gpu_tier(model: dict) -> int:
    """Smallest card that holds the transformer with the usual driver overhead."""
    diffusion = next(f["size_bytes"] for f in model["files"] if f["role"] == "diffusion")
    for tier in GPU_TIERS:
        if diffusion <= 0.87 * tier * GIB:
            return tier
    raise ValueError(f"no GPU tier holds {model['model_id']}")


REVISIONS = {
    "unsloth/Qwen-Image-Edit-2511-GGUF": "0d33d9692b4b26212297240d87b0d4719aa4fd06",
    "mradermacher/Qwen2.5-VL-7B-Instruct-GGUF": "cfa2baa09946b211c107e6e104948987a64dd2c1",
    "Comfy-Org/Qwen-Image_ComfyUI": "1f12b17be14c89b026c51a91d67c32f84bb047bc",
    "leejet/FLUX.2-klein-4B-GGUF": "3b1f5a9dc3abb32238b053aeb3d823c30afdacbd",
    "unsloth/Qwen3-4B-GGUF": "22c9fc8a8c7700b76a1789366280a6a5a1ad1120",
    "Comfy-Org/vae-text-encorder-for-flux-klein-4b": "5f526678002e43af5551dadb73ce2e8c91b43afe",
}


def main():
    metadata = {}
    for repo, revision in REVISIONS.items():
        url = f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true"
        with urllib.request.urlopen(url, timeout=45) as response:
            info = json.load(response)
        if info["sha"] != revision:
            raise ValueError(f"revision mismatch: {repo}")
        metadata[repo] = {f["rfilename"]: f for f in info["siblings"]}

    def file(role, repo, name):
        source = metadata[repo][name]
        lfs = source["lfs"]
        return {
            "role": role,
            "filename": name.rsplit("/", 1)[-1],
            "size_bytes": lfs["size"],
            "sha256": lfs["sha256"],
            "download_url": f"https://huggingface.co/{repo}/resolve/{REVISIONS[repo]}/{name}",
        }

    models = []
    for quant in ("Q2_K", "Q3_K_S", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"):
        encoder = "Q2_K" if quant == "Q2_K" else "Q4_K_M"
        files = [
            file(
                "diffusion",
                "unsloth/Qwen-Image-Edit-2511-GGUF",
                f"qwen-image-edit-2511-{quant}.gguf",
            ),
            file(
                "text_encoder",
                "mradermacher/Qwen2.5-VL-7B-Instruct-GGUF",
                f"Qwen2.5-VL-7B-Instruct.{encoder}.gguf",
            ),
            file(
                "vision",
                "mradermacher/Qwen2.5-VL-7B-Instruct-GGUF",
                "Qwen2.5-VL-7B-Instruct.mmproj-Q8_0.gguf",
            ),
            file(
                "vae", "Comfy-Org/Qwen-Image_ComfyUI", "split_files/vae/qwen_image_vae.safetensors"
            ),
        ]
        models.append(
            {
                "model_id": f"qwen_edit_2511_{quant.lower()}",
                "name": f"Qwen Image Edit 2511 · {quant}",
                "family": "qwen-image-edit-2511",
                "quantization": quant,
                "files": files,
                "default_steps": 40,
                "cfg_scale": 2.5,
                "license_name": "Apache-2.0",
                "license_url": "https://huggingface.co/Qwen/Qwen-Image-Edit-2511/blob/main/LICENSE",
                "source_url": "https://huggingface.co/Qwen/Qwen-Image-Edit-2511",
                "terms_acceptance_required": False,
                "automated_download_allowed": True,
            }
        )
    # Qwen Image 2.1 is not offered: its Qwen Research License allows research and
    # evaluation only, which ordinary photo editing in a public app is not.
    # FLUX.2 klein 4B: a distilled 4-step editor with a Qwen3-4B text encoder and
    # the FLUX.2 autoencoder, all published under Apache-2.0. It is the bundle for
    # 16 GB computers, where the Qwen editors do not fit.
    for quant, encoder in (("Q4_0", "Q4_K_M"), ("Q8_0", "Q8_0")):
        files = [
            file("diffusion", "leejet/FLUX.2-klein-4B-GGUF", f"flux-2-klein-4b-{quant}.gguf"),
            file("text_encoder", "unsloth/Qwen3-4B-GGUF", f"Qwen3-4B-{encoder}.gguf"),
            file(
                "vae",
                "Comfy-Org/vae-text-encorder-for-flux-klein-4b",
                "split_files/vae/flux2-vae.safetensors",
            ),
        ]
        models.append(
            {
                "model_id": f"flux2_klein_4b_{quant.lower()}",
                "name": f"FLUX.2 klein 4B · {quant}",
                "family": "flux2-klein-4b",
                "quantization": quant,
                "files": files,
                "default_steps": 4,
                "cfg_scale": 1.0,
                "license_name": "Apache-2.0",
                "license_url": "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/blob/main/LICENSE.md",
                "source_url": "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B",
                "terms_acceptance_required": False,
                "automated_download_allowed": True,
            }
        )
    for model in models:
        model["min_unified_memory_gb"] = unified_tier(model)
        model["min_vram_gb"] = gpu_tier(model)
    path = ROOT / "src/localsr/core/edit_catalog.json"
    path.write_text(json.dumps(models, indent=2) + "\n", encoding="utf-8")
    print(f"Pinned {len(models)} editing bundles; downloaded metadata only.")


if __name__ == "__main__":
    main()
