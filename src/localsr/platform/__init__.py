"""Platform integration factory for LocalSR."""

import sys

from .base import PlatformService

_PLATFORM_SERVICE: PlatformService | None = None


def get_platform_service() -> PlatformService:
    global _PLATFORM_SERVICE
    if _PLATFORM_SERVICE is None:
        if sys.platform == "darwin":
            from .macos import MacOSPlatformService

            _PLATFORM_SERVICE = MacOSPlatformService()
        elif sys.platform == "win32":
            from .windows import WindowsPlatformService

            _PLATFORM_SERVICE = WindowsPlatformService()
        else:
            from .linux import LinuxPlatformService

            _PLATFORM_SERVICE = LinuxPlatformService()
    return _PLATFORM_SERVICE


def send_notification(title: str, message: str, sound: bool = True) -> bool:
    return get_platform_service().send_notification(title, message, sound)


def install_system_integrations(app_path=None) -> bool:
    return get_platform_service().install_system_integrations(app_path)


def uninstall_system_integrations() -> bool:
    return get_platform_service().uninstall_system_integrations()
