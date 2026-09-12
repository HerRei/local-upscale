from .hardware import get_capability_report


class DeviceManager:
    @staticmethod
    def get_available_devices() -> list[str]:
        return [device["id"] for device in get_capability_report()["devices"]]

    @staticmethod
    def get_default_device() -> str:
        devices = DeviceManager.get_available_devices()
        if "mps" in devices:
            return "mps"
        for backend in ("cuda", "xpu", "directml"):
            for device in devices:
                if device == backend or device.startswith(f"{backend}:"):
                    return device
        return "cpu"

    @staticmethod
    def is_valid_device(device: str) -> bool:
        return device in DeviceManager.get_available_devices()
