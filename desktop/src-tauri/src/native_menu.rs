use tauri::{
    menu::{CheckMenuItemBuilder, MenuBuilder, MenuItemBuilder, SubmenuBuilder},
    App, AppHandle, Emitter, Manager, Runtime,
};

use crate::types::{Recipe, UiSettings};

/// The two update switches the menu shows with a check mark.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Switches {
    pub check_at_launch: bool,
    pub update_count: bool,
}

pub fn switches(settings: &UiSettings) -> Switches {
    Switches {
        check_at_launch: settings.check_updates_at_launch,
        update_count: settings.anonymous_update_count,
    }
}

pub const EVENT_NAME: &str = "native-menu-action";

const PREVIOUS_RESULT_ACCELERATOR: &str = "CmdOrCtrl+Alt+Left";
const NEXT_RESULT_ACCELERATOR: &str = "CmdOrCtrl+Alt+Right";

/// The View menu names the two single-side views after what they show: the
/// Original and the result, or the reference and Showing when both are results.
fn comparison_labels(reference_is_result: bool) -> (&'static str, &'static str) {
    if reference_is_result {
        ("Show Reference", "Show Showing Result")
    } else {
        ("Show Original", "Show Enhanced")
    }
}

/// Rename the two comparison items in place. Menu lookups by id only search
/// one level, so each submenu is asked; a rebuilt menu starts with the
/// Original names until the next comparison request.
pub fn set_comparison_labels(app: &AppHandle, reference_is_result: bool) {
    let Some(menu) = app.menu() else { return };
    let Ok(items) = menu.items() else { return };
    let (reference, showing) = comparison_labels(reference_is_result);
    for (id, label) in [
        ("view.compare-original", reference),
        ("view.compare-enhanced", showing),
    ] {
        for submenu in items.iter().filter_map(|item| item.as_submenu()) {
            if let Some(item) = submenu.get(id).and_then(|item| item.as_menuitem().cloned()) {
                let _ = item.set_text(label);
            }
        }
    }
}

pub fn install<R: Runtime>(
    app: &App<R>,
    recipes: &[Recipe],
    switches: Switches,
) -> tauri::Result<()> {
    install_for_manager(app, recipes, switches)
}

pub fn rebuild<R: Runtime>(
    app: &AppHandle<R>,
    recipes: &[Recipe],
    switches: Switches,
) -> tauri::Result<()> {
    install_for_manager(app, recipes, switches)
}

fn install_for_manager<R: Runtime, M: Manager<R>>(
    manager: &M,
    recipes: &[Recipe],
    switches: Switches,
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
    // Queue-wide, as the footer's Cancel queue; "Cancel runs for this image"
    // is the per-image action in the window.
    let cancel = MenuItemBuilder::with_id("file.cancel", "Cancel Queue")
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
    // Same choices as the preview header's Original | Split | Enhanced switch.
    let show_original =
        MenuItemBuilder::with_id("view.compare-original", "Show Original").build(manager)?;
    let show_split =
        MenuItemBuilder::with_id("view.compare-split", "Show Split Comparison").build(manager)?;
    let show_enhanced =
        MenuItemBuilder::with_id("view.compare-enhanced", "Show Enhanced").build(manager)?;
    // Older and newer saved result on the Showing side. Arrows, not brackets:
    // on Swiss German and German Mac keyboards brackets need Option already.
    let previous = MenuItemBuilder::with_id("view.previous-result", "Previous Result")
        .accelerator(PREVIOUS_RESULT_ACCELERATOR)
        .build(manager)?;
    let next = MenuItemBuilder::with_id("view.next-result", "Next Result")
        .accelerator(NEXT_RESULT_ACCELERATOR)
        .build(manager)?;
    let refresh = MenuItemBuilder::with_id("view.refresh-hardware", "Refresh Hardware Status")
        .build(manager)?;
    // A single ampersand is interpreted as a Windows mnemonic by the native
    // menu library and disappears on macOS, leaving an awkward double space.
    let diagnostics = MenuItemBuilder::with_id("view.performance", "Performance and Diagnostics…")
        .accelerator("CmdOrCtrl+Shift+D")
        .build(manager)?;
    let models = MenuItemBuilder::with_id("view.models", "Models…")
        .accelerator("CmdOrCtrl+Shift+M")
        .build(manager)?;
    let view_menu = SubmenuBuilder::new(manager, "View")
        .item(&fit)
        .item(&actual)
        .separator()
        .item(&show_original)
        .item(&show_split)
        .item(&show_enhanced)
        .item(&previous)
        .item(&next)
        .separator()
        .item(&models)
        .separator()
        .item(&refresh)
        .item(&diagnostics)
        .build()?;

    let guide = MenuItemBuilder::with_id("help.guide", "LocalSR User Guide").build(manager)?;
    let github = MenuItemBuilder::with_id("help.github", "LocalSR on GitHub").build(manager)?;
    // macOS keeps update checks in the app menu; elsewhere they sit under Help.
    let updates = MenuItemBuilder::with_id("app.updates", "Check for Updates…").build(manager)?;
    // Builds without their own update feed (the Store and, for now, Windows and Linux)
    // neither check at launch nor send the count, so they have no switches.
    let switchable = crate::updates::checks_for_updates();
    let check_at_launch =
        CheckMenuItemBuilder::with_id("app.check-updates-at-launch", "Check for Updates at Launch")
            .checked(switches.check_at_launch)
            .build(manager)?;
    let update_count =
        CheckMenuItemBuilder::with_id("app.update-count", "Send Anonymous Update Count")
            .checked(switches.update_count)
            .build(manager)?;
    #[cfg(target_os = "macos")]
    let help_menu = SubmenuBuilder::new(manager, "Help")
        .item(&guide)
        .item(&github)
        .build()?;
    #[cfg(not(target_os = "macos"))]
    let help_menu = {
        let builder = SubmenuBuilder::new(manager, "Help")
            .item(&guide)
            .item(&github)
            .separator()
            .item(&updates);
        if switchable {
            builder.item(&check_at_launch).item(&update_count).build()?
        } else {
            builder.build()?
        }
    };

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
            .item(&updates);
        let app_menu = if switchable {
            app_menu.item(&check_at_launch).item(&update_count)
        } else {
            app_menu
        };
        let app_menu = app_menu
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
        .close_window();
    // Closing the window only hides it on macOS, so the menu needs a way back to it.
    #[cfg(target_os = "macos")]
    let window_menu = window_menu
        .separator()
        .item(&MenuItemBuilder::with_id("window.main", "LocalSR").build(manager)?);
    let window_menu = window_menu.build()?;
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

/// Show the saved update switches. The rebuild runs on the UI thread and reads the settings
/// there, so when several overlap, the last one shows the newest state.
pub fn reconcile(app: &AppHandle) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        let Some(state) = handle.try_state::<std::sync::Arc<crate::state::AppState>>() else {
            return;
        };
        let Ok(switches) = crate::state::lock(&state.settings).map(|settings| switches(&settings))
        else {
            return;
        };
        let Ok(recipes) = crate::state::lock(&state.recipes).map(|recipes| recipes.clone()) else {
            return;
        };
        if let Err(error) = rebuild(&handle, &recipes, switches) {
            eprintln!("LocalSR: could not refresh the update switches in the menu: {error}");
        }
    });
}

pub fn dispatch(app: &AppHandle, id: &str) {
    // The update switches are the host's own settings: it flips the saved value, so a
    // window that has not caught up cannot send the opposite.
    let switch = match id {
        "app.check-updates-at-launch" => Some("check_updates_at_launch"),
        "app.update-count" => Some("anonymous_update_count"),
        _ => None,
    };
    if let Some(name) = switch {
        // Off the menu's thread: saving writes a file.
        let app = app.clone();
        tauri::async_runtime::spawn_blocking(move || {
            if let Some(state) = app.try_state::<std::sync::Arc<crate::state::AppState>>() {
                crate::commands::toggle_update_switch(&state, &app, name);
            }
        });
        return;
    }
    if id == "window.main" {
        crate::focus_main_window(app);
        return;
    }
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

#[cfg(test)]
mod tests {
    #[test]
    fn comparison_menu_names_the_selected_reference_truthfully() {
        assert_eq!(
            super::comparison_labels(false),
            ("Show Original", "Show Enhanced")
        );
        assert_eq!(
            super::comparison_labels(true),
            ("Show Reference", "Show Showing Result")
        );
    }

    #[test]
    fn result_navigation_uses_arrows_that_every_keyboard_layout_can_type() {
        // muda 0.20 reads "Left"/"Right" as the arrow keys (accelerator/mod.rs).
        assert_eq!(super::PREVIOUS_RESULT_ACCELERATOR, "CmdOrCtrl+Alt+Left");
        assert_eq!(super::NEXT_RESULT_ACCELERATOR, "CmdOrCtrl+Alt+Right");
    }
}
