#!/usr/bin/env python3
import sys
import subprocess
import os
import venv
import platform

def run_cmd(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT)
    except Exception:
        return ""

def detect_gpus():
    gpus = []
    os_name = platform.system().lower()
    if os_name == "linux":
        out = run_cmd("lspci | grep -iE 'vga|3d|display'")
        for line in out.splitlines():
            if "vga" in line.lower() or "3d" in line.lower() or "display" in line.lower():
                gpus.append(line.split(":")[-1].strip())
    elif os_name == "windows":
        out = run_cmd("wmic path win32_VideoController get name")
        for line in out.splitlines():
            line = line.strip()
            if line and line.lower() != "name":
                gpus.append(line)
    elif os_name == "darwin":
        gpus.append("Apple Silicon / Mac GPU")
    return gpus

def main():
    print("LocalSR Interactive Installer")
    print("=============================")
    gpus = detect_gpus()
    print("\nDetected Graphics Hardware:")
    if not gpus:
        print("  - No GPU detected or unable to probe hardware.")
    else:
        for gpu in gpus:
            print(f"  - {gpu}")
    
    rec = "4" # CPU default
    rec_name = "CPU Only"
    gpu_str = " ".join(gpus).lower()
    
    if platform.system().lower() == "darwin":
        rec = "4"
        rec_name = "CPU / Apple Silicon (MPS is built-in)"
    elif "nvidia" in gpu_str:
        rec = "1"
        rec_name = "NVIDIA CUDA"
    elif "amd" in gpu_str or "radeon" in gpu_str:
        if platform.system().lower() == "linux":
            rec = "2"
            rec_name = "AMD ROCm"
        else:
            rec = "4"
            rec_name = "CPU Only (AMD ROCm is not officially supported on Windows PyTorch)"
    elif "intel" in gpu_str:
        rec = "3"
        rec_name = "Intel XPU"
    
    print(f"\nSuggested Backend: [{rec}] {rec_name}")
    print("\nPlease select the PyTorch backend to install:")
    print("  1) NVIDIA CUDA (Standard PyTorch)")
    print("  2) AMD ROCm (Linux only)")
    print("  3) Intel XPU (intel-extension-for-pytorch)")
    print("  4) CPU Only / Apple Silicon")
    
    try:
        choice = input(f"\nEnter choice [1-4] (Default: {rec}): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nInstallation cancelled.")
        sys.exit(1)
        
    if not choice:
        choice = rec
        
    print("\nSetting up virtual environment in '.venv'...")
    venv.create(".venv", with_pip=True)
    
    if platform.system().lower() == "windows":
        pip_exe = os.path.join(".venv", "Scripts", "pip")
    else:
        pip_exe = os.path.join(".venv", "bin", "pip")
        
    print("Upgrading pip...")
    subprocess.run([pip_exe, "install", "--upgrade", "pip"], check=True)
    
    print("Installing core LocalSR dependencies...")
    subprocess.run([pip_exe, "install", "-e", "."], check=True)
    
    print("\nUninstalling any existing PyTorch versions to avoid conflicts...")
    subprocess.run([pip_exe, "uninstall", "-y", "torch", "torchvision", "torchaudio", "intel-extension-for-pytorch"], check=False)
    
    print(f"\nInstalling selected PyTorch backend...")
    if choice == "1":
        subprocess.run([pip_exe, "install", "torch", "torchvision"], check=True)
    elif choice == "2":
        if platform.system().lower() == "windows":
            print("Warning: ROCm is generally Linux-only for PyTorch. This might fail or fallback.")
        subprocess.run([pip_exe, "install", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/rocm7.2"], check=True)
    elif choice == "3":
        subprocess.run([pip_exe, "install", "torch", "torchvision"], check=True)
        subprocess.run([pip_exe, "install", "intel-extension-for-pytorch"], check=True)
    elif choice == "4":
        if platform.system().lower() != "darwin":
            subprocess.run([pip_exe, "install", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cpu"], check=True)
        else:
            subprocess.run([pip_exe, "install", "torch", "torchvision"], check=True)
    else:
        print("Invalid choice. Exiting.")
        sys.exit(1)
        
    print("\n=========================================")
    print("Installation complete!")
    if platform.system().lower() == "windows":
        print("Run the application with: .venv\\Scripts\\python -m localsr")
    else:
        print("Run the application with: .venv/bin/python3 -m localsr")

if __name__ == "__main__":
    main()
