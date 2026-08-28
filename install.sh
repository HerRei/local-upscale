#!/usr/bin/env bash
# ==============================================================================
# LocalSR Smart Universal Installer (macOS & Linux)
# Automatically probes hardware (Apple Silicon, NVIDIA CUDA, AMD ROCm, Intel XPU, CPU)
# and installs the optimal standalone binary from GitHub Releases.
# ==============================================================================
set -euo pipefail

REPO="HerRei/local-upscale"
APP_NAME="LocalSR"
INSTALL_DIR_LINUX="${HOME}/.local/share/localsr"
BIN_DIR_LINUX="${HOME}/.local/bin"
DESKTOP_DIR_LINUX="${HOME}/.local/share/applications"
INSTALL_DIR_MACOS="/Applications"

# Colors
BOLD="\033[1m"
GREEN="\033[0;32m"
CYAN="\033[0;36m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

echo -e "${CYAN}${BOLD}"
echo "  _                     _  ____  ____  "
echo " | |    ___   ___ __ _| |/ ___||  _ \ "
echo " | |   / _ \ / __/ _\` | |\___ \| |_) |"
echo " | |__| (_) | (_| (_| | | ___) |  _ < "
echo " |_____\___/ \___\__,_|_||____/|_| \_\\"
echo -e "${NC}"
echo -e "${BOLD}LocalSR Smart Hardware Prober & Installer${NC}\n"

# ------------------------------------------------------------------------------
# 1. Detect Operating System & Hardware Acceleration Flavor
# ------------------------------------------------------------------------------
OS="$(uname -s)"
ARCH="$(uname -m)"
FLAVOR=""

echo -e "🔍 Probing system hardware..."

if [ "${OS}" = "Darwin" ]; then
    echo -e "   Platform: ${GREEN}macOS (${ARCH})${NC}"
    if [ "${ARCH}" = "arm64" ]; then
        echo -e "   Hardware Acceleration: ${GREEN}Apple Silicon GPU (Metal Performance Shaders)${NC}"
        FLAVOR="macOS-arm64"
    else
        echo -e "   Hardware Acceleration: ${YELLOW}Intel CPU (Multi-threaded Accelerate/vecLib)${NC}"
        FLAVOR="macOS-x86_64"
    fi
elif [ "${OS}" = "Linux" ]; then
    echo -e "   Platform: ${GREEN}Linux (${ARCH})${NC}"
    
    # Check NVIDIA CUDA
    if command -v nvidia-smi &>/dev/null && [ -e /dev/nvidia0 ]; then
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1 || echo "NVIDIA GPU")
        echo -e "   Detected GPU: ${GREEN}${GPU_NAME} (CUDA 12.x Acceleration)${NC}"
        FLAVOR="Linux-CUDA"
    # Check AMD ROCm
    elif [ -e /dev/kfd ] && (lspci 2>/dev/null | grep -qi "AMD.*Radeon" || lsmod 2>/dev/null | grep -qi "amdgpu"); then
        echo -e "   Detected GPU: ${GREEN}AMD Radeon (ROCm Acceleration)${NC}"
        FLAVOR="Linux-ROCm"
    # Check Intel Arc / Xe / iGPU
    elif (lspci 2>/dev/null | grep -qiE "Intel.*(Arc|Iris|Graphics|Xe)") && [ -d /dev/dri ]; then
        echo -e "   Detected GPU: ${GREEN}Intel Arc / Iris Xe (XPU & OpenVINO Acceleration)${NC}"
        FLAVOR="Linux-Intel"
    else
        echo -e "   Hardware Acceleration: ${YELLOW}Universal CPU (Optimized SIMD / OpenMP)${NC}"
        FLAVOR="Linux-CPU"
    fi
else
    echo -e "${RED}❌ Unsupported operating system: ${OS}${NC}"
    exit 1
fi

echo -e "   Selected Backend Flavor: ${BOLD}${CYAN}${FLAVOR}${NC}\n"

# ------------------------------------------------------------------------------
# 2. Fetch Latest Matching Release Asset from GitHub
# ------------------------------------------------------------------------------
echo -e "📡 Fetching latest release asset for ${BOLD}${FLAVOR}${NC} from GitHub..."

TMP_DIR=$(mktemp -d /tmp/localsr_install.XXXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT

AUTH_HEADER=()
if [ -n "${GITHUB_TOKEN:-}" ]; then
    AUTH_HEADER=(-H "Authorization: token ${GITHUB_TOKEN}")
fi

# Strategy A: Use GitHub CLI (gh) if authenticated
DOWNLOADED=false
if command -v gh &>/dev/null && gh auth status &>/dev/null; then
    echo -e "   Authenticating via GitHub CLI..."
    PATTERN="*${FLAVOR}*"
    if gh release download --repo "${REPO}" -p "${PATTERN}" -D "${TMP_DIR}" --clobber 2>/dev/null; then
        DOWNLOADED=true
    fi
fi

# Strategy B: Fallback to GitHub REST API
if [ "${DOWNLOADED}" = "false" ]; then
    RELEASES_JSON=$(curl -sSL "${AUTH_HEADER[@]}" "https://api.github.com/repos/${REPO}/releases" 2>/dev/null || true)
    
    DOWNLOAD_URL=$(echo "${RELEASES_JSON}" | grep "browser_download_url" | grep -iE "${FLAVOR}" | head -n 1 | cut -d '"' -f 4 || true)
    
    if [ -z "${DOWNLOAD_URL}" ]; then
        if [ "${OS}" = "Darwin" ]; then
            DOWNLOAD_URL=$(echo "${RELEASES_JSON}" | grep "browser_download_url" | grep -i "macOS" | head -n 1 | cut -d '"' -f 4 || true)
        else
            DOWNLOAD_URL=$(echo "${RELEASES_JSON}" | grep "browser_download_url" | grep -i "Linux-CPU" | head -n 1 | cut -d '"' -f 4 || true)
        fi
    fi
    
    if [ -n "${DOWNLOAD_URL}" ]; then
        FILE_NAME=$(basename "${DOWNLOAD_URL}")
        echo -e "⬇️  Downloading ${BOLD}${FILE_NAME}${NC}..."
        curl -# -L "${AUTH_HEADER[@]}" -o "${TMP_DIR}/${FILE_NAME}" "${DOWNLOAD_URL}"
        DOWNLOADED=true
    fi
fi

ARCHIVE_FILE=$(find "${TMP_DIR}" -type f \( -name "*.zip" -o -name "*.tar.gz" \) | head -n 1)

if [ -z "${ARCHIVE_FILE}" ] || [ "${DOWNLOADED}" = "false" ]; then
    echo -e "${RED}❌ Error: Could not download release package for ${FLAVOR}.${NC}"
    echo -e "Please download directly from: https://github.com/${REPO}/releases"
    exit 1
fi

FILE_NAME=$(basename "${ARCHIVE_FILE}")

CHECKSUM_FILE="${ARCHIVE_FILE}.sha256"
if [ ! -f "${CHECKSUM_FILE}" ] && [ -n "${DOWNLOAD_URL:-}" ]; then
    curl -fsSL "${AUTH_HEADER[@]}" -o "${CHECKSUM_FILE}" "${DOWNLOAD_URL}.sha256"
fi
if [ ! -f "${CHECKSUM_FILE}" ]; then
    echo -e "${RED}❌ Error: Release checksum is missing; refusing to install.${NC}"
    exit 1
fi
echo -e "🔐 Verifying SHA-256 checksum..."
if command -v shasum &>/dev/null; then
    (cd "${TMP_DIR}" && shasum -a 256 -c "$(basename "${CHECKSUM_FILE}")")
elif command -v sha256sum &>/dev/null; then
    (cd "${TMP_DIR}" && sha256sum -c "$(basename "${CHECKSUM_FILE}")")
else
    echo -e "${RED}❌ Error: No SHA-256 verification tool is available.${NC}"
    exit 1
fi

# ------------------------------------------------------------------------------
# 3. Extract and Provision Application
# ------------------------------------------------------------------------------
echo -e "📦 Installing ${BOLD}LocalSR${NC} from ${FILE_NAME}..."

if [ "${OS}" = "Darwin" ]; then
    mkdir -p "${TMP_DIR}/unpacked"
    if [[ "${FILE_NAME}" == *.zip ]]; then
        unzip -q -o "${ARCHIVE_FILE}" -d "${TMP_DIR}/unpacked"
    else
        tar -xzf "${ARCHIVE_FILE}" -C "${TMP_DIR}/unpacked"
    fi
    
    TARGET_APP="${INSTALL_DIR_MACOS}/LocalSR.app"
    if [ ! -w "${INSTALL_DIR_MACOS}" ]; then
        TARGET_APP="${HOME}/Applications/LocalSR.app"
        mkdir -p "${HOME}/Applications"
    fi
    
    rm -rf "${TARGET_APP}"
    cp -R "${TMP_DIR}/unpacked/LocalSR.app" "${TARGET_APP}"
    
    echo -e "${GREEN}✅ LocalSR successfully installed to ${BOLD}${TARGET_APP}${NC}"
else
    mkdir -p "${INSTALL_DIR_LINUX}" "${BIN_DIR_LINUX}" "${DESKTOP_DIR_LINUX}"
    rm -rf "${INSTALL_DIR_LINUX:?}"/*
    tar -xzf "${ARCHIVE_FILE}" -C "${INSTALL_DIR_LINUX}" --strip-components=1 2>/dev/null || tar -xzf "${ARCHIVE_FILE}" -C "${INSTALL_DIR_LINUX}"
    
    chmod +x "${INSTALL_DIR_LINUX}/LocalSR" 2>/dev/null || true
    chmod +x "${INSTALL_DIR_LINUX}/LocalSRWorker" 2>/dev/null || true
    
    ln -sf "${INSTALL_DIR_LINUX}/LocalSR" "${BIN_DIR_LINUX}/localsr"
    
    cat << DESK_EOF > "${DESKTOP_DIR_LINUX}/localsr.desktop"
[Desktop Entry]
Name=LocalSR
Comment=High Performance Local Image & Video Super-Resolution
Exec=${INSTALL_DIR_LINUX}/LocalSR %F
Icon=${INSTALL_DIR_LINUX}/localsr/ui/slint/logo.png
Terminal=false
Type=Application
Categories=Graphics;Photography;Utility;
StartupNotify=true
DESK_EOF
    chmod +x "${DESKTOP_DIR_LINUX}/localsr.desktop"
    
    echo -e "${GREEN}✅ LocalSR successfully installed to ${BOLD}${INSTALL_DIR_LINUX}${NC}"
    echo -e "   Executable linked: ${CYAN}${BIN_DIR_LINUX}/localsr${NC}"
fi

echo -e "\n🎉 ${GREEN}${BOLD}Installation complete!${NC}"
echo -e "Launch LocalSR from your applications menu or terminal to start upscaling."
