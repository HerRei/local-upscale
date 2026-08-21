"""Abstract base class for platform-specific desktop integrations."""

from abc import ABC, abstractmethod
from pathlib import Path


class PlatformService(ABC):
    """Encapsulates OS-level integrations: notifications, context menus, and CLI setup."""

    @abstractmethod
    def send_notification(self, title: str, message: str, sound: bool = True) -> bool:
        """Send a native system notification banner.

        Returns True if the notification was delivered or queued successfully.
        """
        pass

    @abstractmethod
    def install_system_integrations(self, app_path: str | Path | None = None) -> bool:
        """Install OS integrations (Finder Quick Actions, CLI symlinks, etc.)."""
        pass

    @abstractmethod
    def uninstall_system_integrations(self) -> bool:
        """Remove previously installed OS integrations."""
        pass
