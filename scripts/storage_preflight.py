import os
import sys
import shutil

def check_space(path, required_gb, min_percent=20):
    total, used, free = shutil.disk_usage(path)
    total_gb = total / (1024**3)
    free_gb = free / (1024**3)
    free_percent = (free / total) * 100
    
    print(f"Checking {path}:")
    print(f"  Total: {total_gb:.2f} GB")
    print(f"  Free:  {free_gb:.2f} GB ({free_percent:.1f}%)")
    
    if free_gb < required_gb:
        print(f"ERROR: {path} has less than {required_gb} GB free.")
        return False
    if free_percent < min_percent:
        print(f"ERROR: {path} has less than {min_percent}% free space.")
        return False
        
    return True

def clean_old_artifacts(path, max_gb=100):
    # If usage is high, delete older runs
    # This is a stub for the logic
    pass

if __name__ == "__main__":
    success = True
    
    if os.name == 'posix':
        # On Linux/macOS
        # Check root (SSD)
        if not check_space('/', required_gb=10, min_percent=20):
            success = False
            
        # If it's the linux runner, check HDD
        if os.path.exists('/mnt/hdd'):
            if not check_space('/mnt/hdd', required_gb=50, min_percent=10):
                success = False

    if not success:
        sys.exit(1)
