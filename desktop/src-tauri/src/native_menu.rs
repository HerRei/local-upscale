use tauri::{
    menu::{MenuBuilder, MenuItemBuilder, SubmenuBuilder},
    App, AppHandle, Emitter, Manager, Runtime,
};

use crate::types::Recipe;

pub const EVENT_NAME: &str = "native-menu-action";

pub fn install<R: Runtime>(app: &App<R>, recipes: &[Recipe]) -> tauri::Result<()> {
    install_for_manager(app, recipes)
}

pub fn rebuild<R: Runtime>(app: &AppHandle<R>, recipes: &[Recipe]) -> tauri::Result<()> {
    install_for_manager(app, recipes)
}

fn install_for_manager<R: Runtime, M: Manager<R>>(
    manager: &M,
    recipes: &[Recipe],
) -> tauri::Result<()> {
    let add_media = MenuItemBuilder::with_id("file.add-media", "Add Media…")
        .accelerator("CmdOrCtrl+O")
        .build(manager)?;
    let add_folder = MenuItemBuilder::with_id("file.add-folder", "Add Folder…")
        .accelerator("CmdOrCtrl+Shift+O")
        .build(manager)?;
    let open_output = MenuItemBuilder::with_id("file.open-output", "Open Output Folder")
        .accelerator("CmdOrCtrl+E")
        .build(manager)?;
    let clear = MenuItemBuilder::with_id("file.clear", "Clear Media Queue").build(manager)?;
    let start = MenuItemBuilder::with_id("file.start", "Start Processing")
        .accelerator("CmdOrCtrl+R")
        .build(manager)?;
    let cancel = MenuItemBuilder::with_id("file.cancel", "Cancel Job")
        .accelerator("CmdOrCtrl+.")
        .build(manager)?;
    let integrations =
        MenuItemBuilder::with_id("file.integrations", "System Integrations…").build(manager)?;
    let file_menu = SubmenuBuilder::new(manager, "File")
        .item(&add_media)
        .item(&add_folder)
        .item(&open_output)
        .separator()
        .item(&clear)
        .separator()
        .item(&start)
        .item(&cancel)
        .separator()
        .item(&integrations)
        .build()?;

    let quick = MenuItemBuilder::with_id("preset.quick", "Quick Preset (Fast)")
        .accelerator("CmdOrCtrl+1")
        .build(manager)?;
    let best = MenuItemBuilder::with_id("preset.best", "Best Quality Preset")
        .accelerator("CmdOrCtrl+2")
        .build(manager)?;
    let mut presets_builder = SubmenuBuilder::new(manager, "Presets")
        .item(&quick)
        .item(&best);
    if !recipes.is_empty() {
        presets_builder = presets_builder.separator();
    }
    for (index, recipe) in recipes.iter().take(7).enumerate() {
        let item =
            MenuItemBuilder::with_id(format!("preset.recipe.{}", recipe.id), recipe.name.clone())
                .accelerator(format!("CmdOrCtrl+{}", index + 3))
                .build(manager)?;
        presets_builder = presets_builder.item(&item);
    }
    let presets_menu = presets_builder.build()?;

    let fit = MenuItemBuilder::with_id("view.fit", "Fit to Window")
        .accelerator("CmdOrCtrl+0")
        .build(manager)?;
    let actual = MenuItemBuilder::with_id("view.actual", "Actual Pixels (1:1)")
        .accelerator("CmdOrCtrl+Alt+0")
        .build(manager)?;
    let refresh = MenuItemBuilder::with_id("view.refresh-hardware", "Refresh Hardware Status")
        .build(manager)?;
    // A single ampersand is interpreted as a Windows mnemonic by the native
    // menu library and disappears on macOS, leaving an awkward double space.
    let diagnostics = MenuItemBuilder::with_id("view.performance", "Performance and Diagnostics…")
        .accelerator("CmdOrCtrl+Shift+D")
        .build(manager)?;
    let view_menu = SubmenuBuilder::new(manager, "View")
        .item(&fit)
        .item(&actual)
        .separator()
        .item(&refresh)
        .item(&diagnostics)
        .build()?;

    let guide = MenuItemBuilder::with_id("help.guide", "LocalSR User Guide").build(manager)?;
    let github = MenuItemBuilder::with_id("help.github", "LocalSR on GitHub").build(manager)?;
    let help_menu = SubmenuBuilder::new(manager, "Help")
        .item(&guide)
        .item(&github)
        .build()?;

    #[cfg(target_os = "macos")]
    let menu_builder = {
        let app_menu = SubmenuBuilder::new(manager, "LocalSR")
            .about(Some(tauri::menu::AboutMetadata {
                credits: Some(
                    "This software uses libraries from the FFmpeg project under the LGPLv2.1. \
                     LocalSR does not own FFmpeg. Corresponding source and third-party notices \
                     are published with each download."
                        .into(),
                ),
                ..Default::default()
            }))
            .separator()
            .services()
            .separator()
            .hide()
            .hide_others()
            .show_all()
            .separator()
            .quit()
            .build()?;
        MenuBuilder::new(manager).item(&app_menu)
    };
    #[cfg(not(target_os = "macos"))]
    let menu_builder = MenuBuilder::new(manager);

    let window_menu = SubmenuBuilder::new(manager, "Window")
        .minimize()
        .maximize()
        .separator()
        .close_window()
        .build()?;
    let menu = menu_builder
        .item(&file_menu)
        .item(&presets_menu)
        .item(&view_menu)
        .item(&window_menu)
        .item(&help_menu)
        .build()?;
    manager.app_handle().set_menu(menu)?;
    Ok(())
}

pub fn dispatch(app: &AppHandle, id: &str) {
    if id == "help.guide" {
        let _ = open::that_detached(crate::commands::USER_GUIDE_URL);
        return;
    }
    if id == "help.github" {
        let _ = open::that_detached("https://github.com/HerRei/local-upscale");
        return;
    }
    let _ = app.emit(EVENT_NAME, id.to_owned());
}
