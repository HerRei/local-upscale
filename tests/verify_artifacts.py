import os
import sys
import subprocess
import tarfile
import zipfile

def verify_architecture(file_path, expected_arch):
    print(f"Verifying {file_path} for {expected_arch}...")
    if file_path.endswith('.tar.gz'):
        with tarfile.open(file_path, "r:gz") as tar:
            members = [m for m in tar.getmembers() if 'LocalSR' in m.name and '/' not in m.name.strip('/')]
            if not members:
                members = [m for m in tar.getmembers() if m.name.endswith('LocalSR')]
            if not members:
                raise Exception(f"Could not find LocalSR binary in {file_path}")
            bin_member = members[0]
            tar.extract(bin_member, path="/tmp")
            bin_path = os.path.join("/tmp", bin_member.name)
            
            # Check architecture
            out = subprocess.check_output(["file", bin_path]).decode()
            print(f"File output: {out}")
            if expected_arch not in out:
                raise Exception(f"Architecture mismatch! Expected {expected_arch} in {out}")
            os.remove(bin_path)

    elif file_path.endswith('.zip'):
        with zipfile.ZipFile(file_path, "r") as z:
            members = [m for m in z.namelist() if m.endswith('LocalSR.exe')]
            if not members:
                raise Exception(f"Could not find LocalSR.exe in {file_path}")
            bin_member = members[0]
            z.extract(bin_member, path="/tmp")
            bin_path = os.path.join("/tmp", bin_member)
            
            # Check architecture with objdump or file
            out = subprocess.check_output(["file", bin_path]).decode()
            print(f"File output: {out}")
            if expected_arch not in out:
                # 'file' output for PE32+ usually says 'PE32+ executable (GUI) x86-64'
                # For ARM64 it might say 'PE32+ executable (GUI) Aarch64'
                raise Exception(f"Architecture mismatch! Expected {expected_arch} in {out}")
            os.remove(bin_path)

def main():
    if len(sys.argv) < 2:
        print("Usage: verify_artifacts.py <artifact_dir>")
        sys.exit(1)
        
    artifact_dir = sys.argv[1]
    
    for root, dirs, files in os.walk(artifact_dir):
        for f in files:
            file_path = os.path.join(root, f)
            if 'macOS' in f:
                if 'arm64' in f:
                    verify_architecture(file_path, "arm64")
                else:
                    verify_architecture(file_path, "x86_64")
            elif 'Linux' in f:
                verify_architecture(file_path, "x86-64")
            elif 'Windows' in f:
                if 'arm64' in f:
                    verify_architecture(file_path, "aarch64")
                else:
                    verify_architecture(file_path, "x86-64")

if __name__ == "__main__":
    main()
