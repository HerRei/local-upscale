use std::sync::LazyLock;

use serde::Deserialize;
use tauri::{
    menu::{Menu, MenuBuilder, MenuItemBuilder, SubmenuBuilder},
    App, AppHandle, Emitter, Manager, Runtime,
};

use crate::types::Recipe;

/// Pins with a shortcut: Cmd+3 to Cmd+9.
pub const SHORTCUT_PINS: usize = 7;
const RECIPE_ID_PREFIX: &str = "preset.recipe.";
const BROWSE_RECIPES_ID: &str = "preset.browse";
const BROWSE_RECIPES_ACCELERATOR: &str = "CmdOrCtrl+Shift+R";

#[derive(Deserialize)]
struct WorkflowFile {
    workflows: Vec<WorkflowName>,
}

/// The part of a built-in workflow the menu shows: its id and name.
#[derive(Deserialize)]
struct WorkflowName {
    id: String,
    name: String,
}

static WORKFLOWS: LazyLock<Vec<WorkflowName>> = LazyLock::new(|| {
    serde_json::from_str::<WorkflowFile>(include_str!("../resources/workflows.json"))
        .expect("bundled workflows.json must be valid")
        .workflows
});

fn workflow_name(id: &str) -> Option<&'static str> {
    WORKFLOWS
        .iter()
        .find(|workflow| workflow.id == id)
        .map(|workflow| workflow.name.as_str())
}

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

pub fn install<R: Runtime>(app: &App<R>, recipes: &[Recipe], pins: &[String]) -> tauri::Result<()> {
    install_for_manager(app, recipes, pins)
}

pub fn rebuild<R: Runtime>(
    app: &AppHandle<R>,
    recipes: &[Recipe],
    pins: &[String],
) -> tauri::Result<()> {
    install_for_manager(app, recipes, pins)
}

fn install_for_manager<R: Runtime, M: Manager<R>>(
    manager: &M,
    recipes: &[Recipe],
    pins: &[String],
) -> tauri::Result<()> {
    let menu = build_menu(manager, recipes, pins)?;
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

/// Quick and Best, the first pins with Cmd+3 to Cmd+9 in pin order, and Browse Recipes.
/// A recipe that is not pinned has no item; a pin that names no recipe or workflow is
/// skipped and gives its shortcut to the next.
fn presets_items(recipes: &[Recipe], pins: &[String]) -> Vec<LayoutEntry> {
    let mut items = vec![
        entry("preset.quick", "Quick Preset (Fast)", Some("CmdOrCtrl+1")),
        entry("preset.best", "Best Quality Preset", Some("CmdOrCtrl+2")),
        LayoutEntry::Separator,
    ];
    let pinned: Vec<LayoutEntry> = pins
        .iter()
        .filter_map(|id| {
            let name = recipes
                .iter()
                .find(|recipe| &recipe.id == id)
                .map(|recipe| recipe.name.as_str())
                .or_else(|| workflow_name(id))?;
            Some((id, name))
        })
        .take(SHORTCUT_PINS)
        .enumerate()
        .map(|(index, (id, name))| LayoutEntry::Item {
            id: format!("{RECIPE_ID_PREFIX}{id}"),
            label: name.to_owned(),
            accelerator: Some(format!("CmdOrCtrl+{}", index + 3)),
        })
        .collect();
    if !pinned.is_empty() {
        items.extend(pinned);
        items.push(LayoutEntry::Separator);
    }
    items.push(entry(
        BROWSE_RECIPES_ID,
        "Browse Recipes…",
        Some(BROWSE_RECIPES_ACCELERATOR),
    ));
    items
}

fn menu_layout(recipes: &[Recipe], pins: &[String]) -> Vec<SubmenuLayout> {
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

    #[cfg_attr(not(target_os = "macos"), allow(unused_mut))]
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
            items: presets_items(recipes, pins),
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
    pins: &[String],
) -> tauri::Result<Menu<R>> {
    let mut menu_builder = MenuBuilder::new(manager);
    for submenu in menu_layout(recipes, pins) {
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

/// Rebuild the native menu so the saved recipes and pins stay current. The rebuild runs on
/// the UI thread, so when several overlap, the last one shows the newest state.
pub fn reconcile(app: &AppHandle) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        let Some(state) = handle.try_state::<std::sync::Arc<crate::state::AppState>>() else {
            return;
        };
        let Ok(pins) = crate::state::lock(&state.settings).map(|ui| ui.recipe_pins.clone()) else {
            return;
        };
        let Ok(recipes) = crate::state::lock(&state.recipes).map(|recipes| recipes.clone()) else {
            return;
        };
        if let Err(error) = rebuild(&handle, &recipes, &pins) {
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
        let submenus = menu_layout(&[], &[]);
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

    fn recipe(id: &str, name: &str) -> Recipe {
        serde_json::from_value(serde_json::json!({
            "id": id, "name": name, "task": "upscale", "model_id": "m", "output_scale": 2,
            "tile_size": 256, "halo": 16, "precision": "fp32", "safe_memory": false
        }))
        .unwrap()
    }

    fn presets(recipes: &[Recipe], pins: &[&str]) -> Vec<LayoutEntry> {
        let pins: Vec<String> = pins.iter().map(|pin| (*pin).to_owned()).collect();
        menu_layout(recipes, &pins)
            .into_iter()
            .find(|submenu| submenu.title == "Presets")
            .unwrap()
            .items
    }

    const QUICK_AND_BEST: [(&str, &str, &str); 2] = [
        ("preset.quick", "Quick Preset (Fast)", "CmdOrCtrl+1"),
        ("preset.best", "Best Quality Preset", "CmdOrCtrl+2"),
    ];

    fn quick_and_best() -> Vec<LayoutEntry> {
        QUICK_AND_BEST
            .iter()
            .map(|(id, label, accelerator)| entry(id, label, Some(accelerator)))
            .collect()
    }

    fn browse_recipes() -> LayoutEntry {
        entry(
            "preset.browse",
            "Browse Recipes…",
            Some("CmdOrCtrl+Shift+R"),
        )
    }

    #[test]
    fn presets_give_cmd_3_onwards_to_the_pins_in_pin_order_and_end_with_browse_recipes() {
        let recipes = [
            recipe("a", "Phone photos"),
            recipe("b", "Old slides"),
            recipe("c", "Web banners"),
        ];
        let mut expected = quick_and_best();
        expected.extend([
            LayoutEntry::Separator,
            entry("preset.recipe.b", "Old slides", Some("CmdOrCtrl+3")),
            entry(
                "preset.recipe.workflow.old-scan",
                "Old scan",
                Some("CmdOrCtrl+4"),
            ),
            entry("preset.recipe.a", "Phone photos", Some("CmdOrCtrl+5")),
            LayoutEntry::Separator,
            browse_recipes(),
        ]);
        // `c` is not pinned and has no item.
        assert_eq!(
            presets(&recipes, &["b", "workflow.old-scan", "a"]),
            expected
        );

        let mut unpinned = quick_and_best();
        unpinned.extend([LayoutEntry::Separator, browse_recipes()]);
        assert_eq!(presets(&recipes, &[]), unpinned);
        assert_eq!(presets(&[], &[]), unpinned);
    }

    #[test]
    fn only_seven_pins_that_name_a_recipe_or_workflow_get_a_shortcut() {
        let recipes: Vec<Recipe> = (0..9)
            .map(|index| recipe(&format!("r{index}"), &format!("Recipe {index}")))
            .collect();
        // A deleted recipe's id may still come in with a window's older copy of the pins.
        let items = presets(
            &recipes,
            &[
                "deleted",
                "r8",
                "r0",
                "r1",
                "r2",
                "r3",
                "workflow.gone",
                "r4",
                "r5",
                "r6",
                "r7",
            ],
        );
        let shortcuts: Vec<(String, String)> = items
            .iter()
            .filter_map(|item| match item {
                LayoutEntry::Item {
                    id,
                    accelerator: Some(accelerator),
                    ..
                } if id.starts_with(RECIPE_ID_PREFIX) => {
                    Some((id[RECIPE_ID_PREFIX.len()..].to_owned(), accelerator.clone()))
                }
                _ => None,
            })
            .collect();
        let expected: Vec<(String, String)> = ["r8", "r0", "r1", "r2", "r3", "r4", "r5"]
            .iter()
            .zip(3..)
            .map(|(id, key)| ((*id).to_owned(), format!("CmdOrCtrl+{key}")))
            .collect();
        assert_eq!(shortcuts, expected);
        assert_eq!(items.last(), Some(&browse_recipes()));

        let pins: Vec<String> = recipes.iter().map(|recipe| recipe.id.clone()).collect();
        let mut accelerators: Vec<String> = menu_layout(&recipes, &pins)
            .iter()
            .flat_map(|submenu| submenu.items.iter())
            .filter_map(|item| match item {
                LayoutEntry::Item {
                    accelerator: Some(accelerator),
                    ..
                } => Some(accelerator.clone()),
                _ => None,
            })
            .collect();
        let count = accelerators.len();
        accelerators.sort();
        accelerators.dedup();
        assert_eq!(accelerators.len(), count, "a shortcut is used twice");
    }

    #[test]
    fn the_menu_names_the_four_workflows_from_the_file_the_window_reads() {
        for (id, name) in [
            ("workflow.old-scan", "Old scan"),
            ("workflow.low-light", "Low-light phone photo"),
            ("workflow.drawing", "Drawing or anime"),
            ("workflow.web-jpeg", "Web image (JPEG)"),
        ] {
            assert_eq!(workflow_name(id), Some(name));
            assert!(crate::types::is_valid_recipe_list_id(id));
        }
        assert_eq!(WORKFLOWS.len(), 4);
        assert_eq!(workflow_name("workflow.unknown"), None);
    }

    #[test]
    fn result_navigation_uses_arrows_that_every_keyboard_layout_can_type() {
        // muda 0.20 reads "Left"/"Right" as the arrow keys (accelerator/mod.rs).
        assert_eq!(super::PREVIOUS_RESULT_ACCELERATOR, "CmdOrCtrl+Alt+Left");
        assert_eq!(super::NEXT_RESULT_ACCELERATOR, "CmdOrCtrl+Alt+Right");
    }
}
