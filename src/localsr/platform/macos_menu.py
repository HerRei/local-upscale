"""Native macOS Cocoa Menu Bar integration via PyObjC / AppKit."""

import subprocess
import sys
from typing import Any

_HANDLER_INSTANCE = None
_MenuHandlerClass = None

if sys.platform == "darwin":
    try:
        import AppKit
        import objc

        try:
            _MenuHandlerClass = objc.lookUpClass("LocalSRMenuHandler")
        except objc.nosuchclass_error:

            class LocalSRMenuHandler(AppKit.NSObject):
                _app = None
                _callbacks = {}

                @objc.IBAction
                def onMenuAction_(self, sender):
                    tag = int(sender.tag())
                    cb = self._callbacks.get(tag)
                    if cb:
                        try:
                            cb()
                        except Exception as e:
                            print(f"[MenuAction Error] {e}", flush=True)

            _MenuHandlerClass = LocalSRMenuHandler
    except Exception:
        pass


def setup_macos_native_menu(app: Any) -> bool:
    """Build and install native macOS top menu bar connected to SlintApplication callbacks."""
    global _HANDLER_INSTANCE
    if sys.platform != "darwin" or _MenuHandlerClass is None:
        return False

    try:
        import AppKit

        handler = _MenuHandlerClass.alloc().init()
        handler._app = app
        handler._callbacks = {}
        _HANDLER_INSTANCE = handler
        tag_counter = 100

        ns_app = AppKit.NSApplication.sharedApplication()
        main_menu = AppKit.NSMenu.alloc().initWithTitle_("MainMenu")

        def add_top_menu(title: str) -> Any:
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            menu = AppKit.NSMenu.alloc().initWithTitle_(title)
            item.setSubmenu_(menu)
            main_menu.addItem_(item)
            return menu

        def add_item(
            menu: Any,
            title: str,
            key_equiv: str = "",
            action: str = "onMenuAction:",
            cb: Any = None,
            shift: bool = False,
            alt: bool = False,
        ) -> Any:
            nonlocal tag_counter
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, action, key_equiv
            )
            mask = 0
            if shift:
                mask |= AppKit.NSEventModifierFlagShift
            if alt:
                mask |= AppKit.NSEventModifierFlagOption
            if mask:
                item.setKeyEquivalentModifierMask_(mask | AppKit.NSEventModifierFlagCommand)
            if cb:
                tag_counter += 1
                handler._callbacks[tag_counter] = cb
                item.setTag_(tag_counter)
                item.setTarget_(handler)
            menu.addItem_(item)
            return item

        # 1. LocalSR App Menu
        app_menu = add_top_menu("LocalSR")

        add_item(
            app_menu,
            "About LocalSR",
            action="orderFrontStandardAboutPanel:",
        )
        app_menu.addItem_(AppKit.NSMenuItem.separatorItem())

        services_item = add_item(app_menu, "Services")
        services_menu = AppKit.NSMenu.alloc().initWithTitle_("Services")
        services_item.setSubmenu_(services_menu)
        ns_app.setServicesMenu_(services_menu)

        app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        add_item(app_menu, "Hide LocalSR", "h", action="hide:")
        add_item(app_menu, "Hide Others", "h", action="hideOtherApplications:", alt=True)
        add_item(app_menu, "Show All", action="unhideAllApplications:")
        app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        add_item(app_menu, "Quit LocalSR", "q", action="terminate:")

        # 2. File Menu
        file_menu = add_top_menu("File")

        add_item(file_menu, "Add Media…", "o", cb=getattr(app, "choose_images", None))
        add_item(file_menu, "Add Folder…", "o", shift=True, cb=getattr(app, "choose_folder", None))
        add_item(file_menu, "Open Output Folder", "e", cb=getattr(app, "open_output_folder", None))
        file_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        add_item(file_menu, "Clear Media Queue", cb=getattr(app, "clear_queue", None))
        file_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        add_item(file_menu, "Start Upscaling", "r", cb=getattr(app, "start_jobs", None))
        add_item(file_menu, "Cancel Job", ".", cb=getattr(app, "cancel_job", None))

        # 3. Presets Menu
        presets_menu = add_top_menu("Presets")

        add_item(
            presets_menu,
            "Quick Preset (Fast)",
            "1",
            cb=lambda: getattr(app, "apply_automatic_setup", lambda **kw: None)(best=False),
        )
        add_item(
            presets_menu,
            "Best Quality Preset",
            "2",
            cb=lambda: getattr(app, "apply_automatic_setup", lambda **kw: None)(best=True),
        )

        custom_recipes = getattr(app, "custom_recipes", [])
        if custom_recipes:
            presets_menu.addItem_(AppKit.NSMenuItem.separatorItem())
            for idx, r in enumerate(custom_recipes[:9]):
                recipe_name = str(r.get("name", f"Recipe {idx + 1}"))
                key = str(idx + 3) if idx + 3 <= 9 else ""

                def _make_recipe_cb(index=idx):
                    return lambda: getattr(app, "apply_recipe", lambda i: None)(index)

                add_item(presets_menu, recipe_name, key, cb=_make_recipe_cb(idx))

        # 4. View Menu
        view_menu = add_top_menu("View")

        add_item(view_menu, "Refresh Hardware Status", cb=getattr(app, "refresh_hardware", None))

        # 5. Window Menu
        window_menu = add_top_menu("Window")

        add_item(window_menu, "Minimize", "m", action="performMiniaturize:")
        add_item(window_menu, "Zoom", action="performZoom:")
        window_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        add_item(window_menu, "Bring All to Front", action="arrangeInFront:")
        ns_app.setWindowsMenu_(window_menu)

        # 6. Help Menu
        help_menu = add_top_menu("Help")

        def open_github():
            subprocess.Popen(["open", "https://github.com/HerRei/local-upscale"])

        add_item(help_menu, "LocalSR on GitHub", cb=open_github)
        ns_app.setHelpMenu_(help_menu)

        # Assign as global macOS main menu
        ns_app.setMainMenu_(main_menu)
        app._macos_menu_handler = handler
        return True
    except Exception as e:
        print(f"[setup_macos_native_menu error] {e}", flush=True)
        return False
