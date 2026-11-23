# SPDX-License-Identifier: Apache-2.0
# Modified for LocalSR: minimal PyTorch FlashVSR v1.1 Tiny package entry point.
"""Minimal vendored PyTorch FlashVSR v1.1 Tiny pipeline for AMD ROCm."""

from .pipeline import FlashVSRTinyPipeline

__all__ = ["FlashVSRTinyPipeline"]
