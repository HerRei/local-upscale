#!/usr/bin/env bash
set -e

echo "LocalSR Linux Installation Script"
echo "================================="

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "Installing core dependencies..."
pip install -e .

echo "Detecting GPU..."
if command -v lspci &> /dev/null; then
    GPU_INFO=$(lspci | grep -iE 'vga|3d|display')
    if echo "$GPU_INFO" | grep -qi "amd\|radeon"; then
        echo "AMD GPU detected. Installing ROCm PyTorch..."
        pip uninstall -y torch torchvision torchaudio || true
        pip install torch torchvision --index-url https://download.pytorch.org/whl/rocm7.2
    elif echo "$GPU_INFO" | grep -qi "nvidia"; then
        echo "NVIDIA GPU detected. Standard PyTorch CUDA version is already installed."
    elif echo "$GPU_INFO" | grep -qi "intel"; then
        echo "Intel GPU detected. Installing Intel Extension for PyTorch (XPU)..."
        pip install intel-extension-for-pytorch
    else
        echo "No supported dedicated GPU detected. Defaulting to CPU/CUDA PyTorch."
    fi
else
    echo "lspci not found. Proceeding with default PyTorch."
fi

echo "Installation complete!"
echo "Run the application with: source .venv/bin/activate && python -m localsr"
