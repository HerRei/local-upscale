use tauri::{
    menu::{Menu, MenuBuilder, MenuItemBuilder, SubmenuBuilder},
    App, AppHandle, Emitter, Manager, Runtime,
};

use crate::types::Recipe;

pub const EVENT_NAME: &str = "native-menu-action";
const REPORT_PROBLEM_ID: &str = "help.report-problem";
pub const SETTINGS_ID: &str = "app.settings";
pub const SETTINGS_ACCELERATOR: &str = "CmdOrCtrl+,";

/// The update entry in menu order: macOS shows it in the LocalSR menu, Windows and
/// Linux at the end of Help.
fn update_items() -> &'static [&'static str] {
    &["app.updates"]
}

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
    let menu = build_menu(manager, recipes)?;
    manager.app_handle().set_menu(menu)?;
    Ok(())
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum LayoutEntry {
    Item {
        id: String,
        label: String,
        accelerator: Option<String>,
    },
    Separator,
    // The app menu exists on macOS only; elsewhere the window's own controls do this.
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    About,
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    Services,
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    Hide,
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    HideOthers,
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    ShowAll,
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    Quit,
    Minimize,
    Maximize,
    CloseWindow,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct SubmenuLayout {
    title: &'static str,
    items: Vec<LayoutEntry>,
}

fn entry(id: &str, label: &str, accelerator: Option<&str>) -> LayoutEntry {
    LayoutEntry::Item {
        id: id.to_owned(),
        label: label.to_owned(),
        accelerator: accelerator.map(str::to_owned),
    }
}

fn menu_layout(recipes: &[Recipe]) -> Vec<SubmenuLayout> {
    #[cfg_attr(target_os = "macos", allow(unused_mut))]
    let mut file_items = vec![
        entry("file.add-media", "Add Media…", Some("CmdOrCtrl+O")),
        entry("file.add-folder", "Add Folder…", Some("CmdOrCtrl+Shift+O")),
        entry(
            "file.open-output",
            "Open Output Folder",
            Some("CmdOrCtrl+E"),
        ),
        LayoutEntry::Separator,
        entry("file.clear", "Clear Media Queue", None),
        LayoutEntry::Separator,
        entry("file.start", "Start Processing", Some("CmdOrCtrl+R")),
        // Queue-wide, as the footer's Cancel queue; "Cancel runs for this image"
        // is the per-image action in the window.
        entry("file.cancel", "Cancel Queue", Some("CmdOrCtrl+.")),
    ];
    #[cfg(not(target_os = "macos"))]
    {
        file_items.push(LayoutEntry::Separator);
        file_items.push(entry(SETTINGS_ID, "Settings…", Some(SETTINGS_ACCELERATOR)));
    }

    let mut presets_items = vec![
        entry("preset.quick", "Quick Preset (Fast)", Some("CmdOrCtrl+1")),
        entry("preset.best", "Best Quality Preset", Some("CmdOrCtrl+2")),
    ];
    if !recipes.is_empty() {
        presets_items.push(LayoutEntry::Separator);
    }
    for (index, recipe) in recipes.iter().take(7).enumerate() {
        presets_items.push(LayoutEntry::Item {
            id: format!("preset.recipe.{}", recipe.id),
            label: recipe.name.clone(),
            accelerator: Some(format!("CmdOrCtrl+{}", index + 3)),
        });
    }

    let view_items = vec![
        entry("view.fit", "Fit to Window", Some("CmdOrCtrl+0")),
        entry(
            "view.actual",
            "Actual Pixels (1:1)",
            Some("CmdOrCtrl+Alt+0"),
        ),
        LayoutEntry::Separator,
        // Same choices as the preview header's Original | Split | Enhanced switch.
        entry("view.compare-original", "Show Original", None),
        entry("view.compare-split", "Show Split Comparison", None),
        entry("view.compare-enhanced", "Show Enhanced", None),
        // Older and newer saved result on the Showing side. Arrows, not brackets:
        // on Swiss German and German Mac keyboards brackets need Option already.
        entry(
            "view.previous-result",
            "Previous Result",
            Some(PREVIOUS_RESULT_ACCELERATOR),
        ),
        entry(
            "view.next-result",
            "Next Result",
            Some(NEXT_RESULT_ACCELERATOR),
        ),
        LayoutEntry::Separator,
        entry("view.models", "Models…", Some("CmdOrCtrl+Shift+M")),
        LayoutEntry::Separator,
        entry("view.refresh-hardware", "Refresh Hardware Status", None),
        // A single ampersand is interpreted as a Windows mnemonic by the native
        // menu library and disappears on macOS, leaving an awkward double space.
        entry(
            "view.performance",
            "Performance and Diagnostics…",
            Some("CmdOrCtrl+Shift+D"),
        ),
    ];

    let mut window_items = vec![
        LayoutEntry::Minimize,
        LayoutEntry::Maximize,
        LayoutEntry::Separator,
        LayoutEntry::CloseWindow,
    ];
    // Closing the window only hides it on macOS, so the menu needs a way back to it.
    #[cfg(target_os = "macos")]
    {
        window_items.push(LayoutEntry::Separator);
        window_items.push(entry("window.main", "LocalSR", None));
    }

    #[cfg_attr(target_os = "macos", allow(unused_mut))]
    let mut help_items = vec![
        entry("help.guide", "LocalSR User Guide", None),
        entry(REPORT_PROBLEM_ID, "Report a problem…", None),
        entry("help.github", "LocalSR on GitHub", None),
    ];
    #[cfg(not(target_os = "macos"))]
    {
        help_items.push(LayoutEntry::Separator);
        help_items.push(entry(update_items()[0], "Check for Updates…", None));
    }

    vec![
        #[cfg(target_os = "macos")]
        SubmenuLayout {
            title: "LocalSR",
            items: vec![
                LayoutEntry::About,
                LayoutEntry::Separator,
                entry(SETTINGS_ID, "Settings…", Some(SETTINGS_ACCELERATOR)),
                LayoutEntry::Separator,
                entry(update_items()[0], "Check for Updates…", None),
                LayoutEntry::Separator,
                LayoutEntry::Services,
                LayoutEntry::Separator,
                LayoutEntry::Hide,
                LayoutEntry::HideOthers,
                LayoutEntry::ShowAll,
                LayoutEntry::Separator,
                LayoutEntry::Quit,
            ],
        },
        SubmenuLayout {
            title: "File",
            items: file_items,
        },
        SubmenuLayout {
            title: "Presets",
            items: presets_items,
        },
        SubmenuLayout {
            title: "View",
            items: view_items,
        },
        SubmenuLayout {
            title: "Window",
            items: window_items,
        },
        SubmenuLayout {
            title: "Help",
            items: help_items,
        },
    ]
}

fn build_menu<R: Runtime, M: Manager<R>>(
    manager: &M,
    recipes: &[Recipe],
) -> tauri::Result<Menu<R>> {
    let mut menu_builder = MenuBuilder::new(manager);
    for submenu in menu_layout(recipes) {
        let mut builder = SubmenuBuilder::new(manager, submenu.title);
        for item in submenu.items {
            builder = match item {
                LayoutEntry::Item {
                    id,
                    label,
                    accelerator,
                } => {
                    let mut item_builder = MenuItemBuilder::with_id(id, label);
                    if let Some(accel) = accelerator {
                        item_builder = item_builder.accelerator(accel);
                    }
                    let built = item_builder.build(manager)?;
                    builder.item(&built)
                }
                LayoutEntry::Separator => builder.separator(),
                LayoutEntry::About => builder.about(Some(tauri::menu::AboutMetadata {
                    credits: Some(
                        "This software uses libraries from the FFmpeg project under the LGPLv2.1. \
                         LocalSR does not own FFmpeg. Corresponding source and third-party notices \
                         are published with each download."
                            .into(),
                    ),
                    ..Default::default()
                })),
                LayoutEntry::Services => builder.services(),
                LayoutEntry::Hide => builder.hide(),
                LayoutEntry::HideOthers => builder.hide_others(),
                LayoutEntry::ShowAll => builder.show_all(),
                LayoutEntry::Quit => builder.quit(),
                LayoutEntry::Minimize => builder.minimize(),
                LayoutEntry::Maximize => builder.maximize(),
                LayoutEntry::CloseWindow => builder.close_window(),
            };
        }
        let built = builder.build()?;
        menu_builder = menu_builder.item(&built);
    }
    menu_builder.build()
}

/// Rebuild the native menu so the saved recipes stay current. The rebuild runs on the
/// UI thread, so when several overlap, the last one shows the newest state.
pub fn reconcile(app: &AppHandle) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        let Some(state) = handle.try_state::<std::sync::Arc<crate::state::AppState>>() else {
            return;
        };
        let Ok(recipes) = crate::state::lock(&state.recipes).map(|recipes| recipes.clone()) else {
            return;
        };
        if let Err(error) = rebuild(&handle, &recipes) {
            eprintln!("LocalSR: could not refresh the menu: {error}");
        }
    });
}

pub fn dispatch(app: &AppHandle, id: &str) {
    if crate::tray::handle_menu_event(app, id) {
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
    use super::*;

    fn collect_menu_ids(submenus: &[SubmenuLayout]) -> Vec<String> {
        submenus
            .iter()
            .flat_map(|sub| sub.items.iter())
            .filter_map(|entry| match entry {
                LayoutEntry::Item { id, .. } => Some(id.clone()),
                _ => None,
            })
            .collect()
    }

    #[test]
    fn report_problem_has_a_stable_menu_id() {
        assert_eq!(REPORT_PROBLEM_ID, "help.report-problem");
    }

    #[test]
    fn update_items_keeps_only_the_update_check() {
        assert_eq!(update_items(), ["app.updates"]);
    }

    #[test]
    fn settings_item_exists_with_shortcut_and_removed_ids_are_absent() {
        let submenus = menu_layout(&[]);
        let ids = collect_menu_ids(&submenus);
        assert!(ids.contains(&SETTINGS_ID.to_owned()));
        assert_eq!(SETTINGS_ID, "app.settings");
        assert_eq!(SETTINGS_ACCELERATOR, "CmdOrCtrl+,");
        for removed in [
            "file.integrations",
            "view.appearance.dark",
            "view.appearance.light",
            "view.appearance.system",
            "view.colour-blind",
            "help.crash-reports",
            "app.check-updates-at-launch",
            "app.update-count",
            "help.add-desktop-menu",
            "help.remove-desktop-menu",
        ] {
            assert!(
                !ids.contains(&removed.to_owned()),
                "unexpected id: {removed}"
            );
        }

        #[cfg(target_os = "macos")]
        {
            let app_menu = submenus.iter().find(|s| s.title == "LocalSR").unwrap();
            assert_eq!(
                &app_menu.items[..5],
                &[
                    LayoutEntry::About,
                    LayoutEntry::Separator,
                    entry(SETTINGS_ID, "Settings…", Some(SETTINGS_ACCELERATOR)),
                    LayoutEntry::Separator,
                    entry("app.updates", "Check for Updates…", None),
                ]
            );
        }
        #[cfg(not(target_os = "macos"))]
        {
            let file_menu = submenus.iter().find(|s| s.title == "File").unwrap();
            let tail = &file_menu.items[file_menu.items.len() - 2..];
            assert_eq!(
                tail,
                &[
                    LayoutEntry::Separator,
                    entry(SETTINGS_ID, "Settings…", Some(SETTINGS_ACCELERATOR)),
                ]
            );
        }
    }

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
