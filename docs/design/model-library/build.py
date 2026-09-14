#!/usr/bin/env python3
"""Generate the Model Library design artboards (.dc.html) and canvas.json.

Every artboard reproduces the Tauri/Svelte desktop app's visual vocabulary
(desktop/src/styles.css): Inter, the #14171b/#1b1d22/#21242a surfaces, 6–7 px
radii, 30 px controls, the #4e8cff accent, Quick in #9dc0fd and Best in #e8c47e.
Model names, sizes, licences and filenames come from the real catalog JSON.

Run:  python3 build.py   → writes ./artboards/*.dc.html and ./artboards/canvas.json
"""
from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "artboards"
OUT.mkdir(exist_ok=True)

CATALOG = json.loads(
    (HERE / "../../../desktop/src-tauri/resources/model-catalog.json").resolve().read_text()
)
MODELS = {m["model_id"]: m for m in CATALOG["models"]}


def mb(model_id: str) -> str:
    size = MODELS[model_id]["size_bytes"] / 1_000_000
    return f"{size:.1f} MB" if size < 10 else f"{size:.0f} MB"


# --------------------------------------------------------------------------- CSS
FONT = (
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Inter:wght@400..700&family=Patrick+Hand&display=swap">'
)

CSS = r"""
* { box-sizing: border-box; }
body { margin: 0; background: #14171b; color: #f0f1f4; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 12px; line-height: 1.3; -webkit-font-smoothing: antialiased; }
a { color: #9dc0fd; text-decoration: none; } a:hover { color: #b9d0ff; }
button { font: inherit; color: inherit; border: 0; background: transparent; padding: 0; text-align: left; }
svg { flex: none; }
h1, h2, h3, p, dl, dd { margin: 0; }

/* shell */
.app-shell { display: grid; grid-template-rows: 46px 1fr 54px; overflow: hidden; background: #14171b; position: relative; }
.toolbar { position: relative; display: flex; align-items: center; justify-content: flex-end; padding: 0 16px; background: #21242a; border-top: 1px solid rgba(255,255,255,.04); border-bottom: 1px solid #08090b; }
.brand-mark { position: absolute; left: 14px; display: flex; gap: 3px; align-items: end; width: 30px; height: 30px; padding: 4px; border-radius: 6px; opacity: .72; }
.brand-mark i { display: block; width: 5px; border-radius: 2px; background: linear-gradient(#6a9efe,#346dd7); }
.brand-mark i:nth-child(1) { height: 10px; } .brand-mark i:nth-child(2) { height: 18px; } .brand-mark i:nth-child(3) { height: 14px; }
.toolbar-actions { display: flex; align-items: center; gap: 8px; }
.button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 30px; padding: 0 12px; border: 1px solid rgba(255,255,255,.08); border-radius: 6px; background: #282b32; color: #f0f1f4; font-size: 12px; font-weight: 550; box-shadow: inset 0 1px rgba(255,255,255,.035); white-space: nowrap; }
.button.primary { border-color: transparent; background: linear-gradient(#447eea,#346dd7); color: #fff; }
.button.compact { min-height: 28px; } .button.full { width: 100%; margin-top: 8px; }
.button.ghost { border-color: transparent; background: transparent; box-shadow: none; }
.button.danger { color: #ffacae; border-color: rgba(229,72,77,.28); background: rgba(229,72,77,.09); }
.button.disabled { opacity: .38; }
.benchmark-shortcut { color: #b9d0ff; border-color: rgba(78,140,255,.28); background: rgba(78,140,255,.08); }
.workspace { min-height: 0; overflow: hidden; display: grid; grid-template-columns: 276px minmax(380px,1fr) 340px; }
.pane { min-width: 0; min-height: 0; background: #1b1d22; }
.media-pane { display: flex; flex-direction: column; border-right: 1px solid #08090b; }
.enhance-pane { display: flex; flex-direction: column; border-left: 1px solid #08090b; overflow: hidden; }
.pane-heading { flex: 0 0 48px; display: flex; align-items: center; justify-content: space-between; padding: 0 16px; }
.pane-heading h1 { font-size: 15px; font-weight: 600; letter-spacing: -.1px; }
.pane-heading span { color: #9da1a8; font-size: 11px; }

/* media pane */
.segmented { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; margin: 0 12px 12px; padding: 3px; border-radius: 8px; background: #16181c; border: 1px solid rgba(255,255,255,.06); }
.segmented button, .seg-inline button { min-height: 30px; display: grid; place-items: center; border-radius: 5px; color: #9da1a8; font-size: 12px; }
.segmented button.active, .seg-inline button.active { background: #282b32; color: #f0f1f4; box-shadow: inset 0 1px rgba(255,255,255,.06); }
.mode-scope { min-height: 30px; margin: -5px 16px 8px; color: #9da1a8; font-size: 11px; line-height: 1.35; }
.media-list { flex: 1; min-height: 0; overflow: hidden; padding: 0 8px; }
.media-row { position: relative; width: 100%; height: 64px; display: flex; align-items: center; gap: 4px; padding: 0 8px 0 10px; border-radius: 8px; }
.media-row.selected { background: rgba(78,140,255,.18); }
.media-row.selected::before { content: ""; position: absolute; left: 0; top: 8px; bottom: 8px; width: 3px; border-radius: 2px; background: linear-gradient(#6a9efe,#4e8cff); }
.media-select { min-width: 0; height: 100%; flex: 1; display: flex; align-items: center; gap: 10px; }
.thumb { flex: 0 0 44px; height: 44px; display: grid; place-items: center; overflow: hidden; border: 1px solid rgba(255,255,255,.08); border-radius: 6px; background: #282b32; color: #9da1a8; font-size: 11px; }
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.media-copy { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.media-copy strong { font-size: 13px; font-weight: 550; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.media-copy span { color: #9da1a8; font-size: 11px; }
.remove { flex: 0 0 24px; display: grid; place-items: center; width: 24px; height: 24px; border-radius: 5px; color: #7a8089; }
.media-actions { display: flex; flex-wrap: wrap; gap: 7px; padding: 12px; border-top: 1px solid rgba(255,255,255,.04); }

/* preview pane */
.preview-pane { min-width: 0; min-height: 0; display: grid; grid-template-rows: 48px 1fr; background: #14171b; }
.preview-header { display: flex; align-items: center; justify-content: space-between; padding: 0 16px; border-bottom: 1px solid rgba(255,255,255,.04); background: #181b1f; }
.preview-header > div { display: flex; flex-direction: column; gap: 2px; }
.preview-header strong { font-size: 13px; font-weight: 600; } .preview-header span { color: #9da1a8; font-size: 11px; }
.canvas-well { position: relative; margin: 10px; overflow: hidden; display: grid; place-items: center; border: 1px solid rgba(255,255,255,.04); border-radius: 8px; background-color: #0c0d10; background-image: linear-gradient(45deg,#111318 25%,transparent 25%),linear-gradient(-45deg,#111318 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#111318 75%),linear-gradient(-45deg,transparent 75%,#111318 75%); background-size: 24px 24px; background-position: 0 0,0 12px,12px -12px,-12px 0; box-shadow: inset 0 2px 8px rgba(0,0,0,.4); }
.image-stage { position: relative; box-shadow: 0 18px 60px rgba(0,0,0,.5); } .image-stage img { display: block; }
.zoom-hud { position: absolute; right: 14px; top: 14px; display: flex; min-height: 34px; padding: 3px; border: 1px solid rgba(255,255,255,.1); border-radius: 8px; background: rgba(22,24,28,.9); box-shadow: 0 8px 22px rgba(0,0,0,.4); }
.zoom-hud button, .zoom-hud span { min-width: 34px; display: grid; place-items: center; border-radius: 5px; color: #d9dce2; font-size: 11px; }
.zoom-hud span { min-width: 52px; color: #9da1a8; } .zoom-hud button.active { background: rgba(78,140,255,.2); }

/* enhance pane */
.inspector-scroll { flex: 1 1 0; min-height: 0; overflow: hidden; padding: 0 16px 32px; }
.control-section { padding: 14px 0; border-bottom: 1px solid rgba(255,255,255,.055); }
.eyebrow { display: flex; justify-content: space-between; align-items: center; margin-bottom: 9px; color: #9da1a8; font-size: 11px; font-weight: 650; letter-spacing: .75px; }
.eyebrow a, .eyebrow span { font-weight: 500; letter-spacing: 0; }
.task-grid, .recipe-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
.task-grid button, .recipe-grid button { min-height: 62px; padding: 10px; display: flex; flex-direction: column; justify-content: center; gap: 4px; border: 1px solid rgba(255,255,255,.08); border-radius: 7px; background: #282b32; }
.task-grid button.active, .recipe-grid button.active { border-color: rgba(78,140,255,.55); background: rgba(78,140,255,.13); box-shadow: inset 0 0 0 1px rgba(78,140,255,.08); }
.recipe-grid button.best.active { border-color: rgba(232,196,126,.5); background: rgba(232,196,126,.1); box-shadow: inset 0 0 0 1px rgba(232,196,126,.08); }
.task-grid b, .recipe-grid b { font-size: 13px; font-weight: 600; }
.task-grid span, .recipe-grid span { color: #9da1a8; font-size: 11px; }
.task-grid .video-task { grid-column: 1 / -1; min-height: 54px; } .task-grid .video-task span { color: #9dc0fd; }
.recipe-grid .quick b { color: #9dc0fd; } .recipe-grid .best b { color: #e8c47e; }
.save-recipe { display: flex; align-items: center; justify-content: center; gap: 6px; width: 100%; min-height: 34px; margin-top: 9px; border: 1px dashed rgba(157,192,253,.34); border-radius: 6px; background: rgba(78,140,255,.06); color: #9dc0fd; font-size: 11px; }
.field-label { display: block; margin: 10px 0 5px; color: #9da1a8; font-size: 11px; } .field-label:first-child { margin-top: 0; }
.seg-inline { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; padding: 3px; border-radius: 8px; background: #16181c; border: 1px solid rgba(255,255,255,.06); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { display: inline-flex; align-items: center; gap: 6px; min-height: 28px; padding: 0 10px; border: 1px solid rgba(255,255,255,.09); border-radius: 14px; background: #16181c; color: #9da1a8; font-size: 11px; }
.chip.on { border-color: rgba(78,140,255,.55); background: rgba(78,140,255,.13); color: #f0f1f4; } .chip.on svg { color: #9dc0fd; }
.plan { border: 1px solid rgba(255,255,255,.08); border-radius: 6px; background: #16181c; overflow: hidden; }
.plan-row { display: grid; grid-template-columns: 66px 1fr auto; gap: 10px; align-items: start; padding: 10px; border-bottom: 1px solid rgba(255,255,255,.055); }
.plan-row:last-child { border-bottom: 0; }
.plan-row .stage { color: #9da1a8; font-size: 11px; padding-top: 1px; }
.plan-row .name { font-size: 12px; font-weight: 550; } .plan-row .meta { margin-top: 3px; color: #9da1a8; font-size: 11px; line-height: 1.4; }
.plan-row .state { display: flex; align-items: center; gap: 4px; font-size: 11px; color: #9da1a8; white-space: nowrap; }
.state.ok { color: #91cca1; } .state.warn { color: #e7bc72; }
.plan-row .full-span { grid-column: 2 / -1; }
.model-note { margin-top: 8px; color: #9da1a8; font-size: 11px; line-height: 1.45; }
.inline-warning { margin: 8px 0 0; color: #e7bc72; font-size: 11px; line-height: 1.4; }
.download-track { display: block; height: 3px; margin-top: 7px; overflow: hidden; border-radius: 2px; background: #0f1114; }
.download-track i { display: block; height: 100%; background: linear-gradient(90deg,#346dd7,#6a9efe); }
.terms { display: flex; align-items: flex-start; gap: 7px; margin: 9px 0 0; color: #9da1a8; font-size: 11px; line-height: 1.4; }
.box { flex: none; width: 13px; height: 13px; margin-top: 1px; border: 1px solid rgba(255,255,255,.25); border-radius: 3px; background: #16181c; }
.disclosure { position: relative; width: 100%; height: 48px; }
.disclosure span { position: absolute; left: 0; top: 4px; font-size: 13px; font-weight: 600; }
.disclosure small { position: absolute; left: 0; top: 26px; color: #9da1a8; font-size: 11px; }
.disclosure b { position: absolute; right: 2px; top: 13px; color: #9da1a8; display: grid; }
.summary-card dl div { display: grid; grid-template-columns: 70px 1fr; gap: 8px; padding: 5px 0; }
.summary-card dt { color: #9da1a8; font-size: 11px; }
.summary-card dd { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: right; color: #9da1a8; font-size: 11px; }
.about-block { padding: 17px 0; color: #9da1a8; }
.about-block > div { display: flex; justify-content: space-between; }
.about-block b { color: #f0f1f4; font-size: 12px; font-weight: 600; } .about-block span { font-size: 11px; }
.about-block .links { display: flex; gap: 10px; margin-top: 9px; } .about-block a { font-size: 11px; }
.about-block p { margin-top: 8px; font-size: 11px; line-height: 1.5; }
.notice { padding: 10px 12px; border: 1px solid rgba(78,140,255,.28); border-radius: 8px; background: rgba(16,22,33,.72); }
.notice b { display: block; font-size: 12px; font-weight: 600; color: #b9d0ff; }
.notice p { margin-top: 4px; color: #9da1a8; font-size: 11px; line-height: 1.45; }
.notice .actions-row { display: flex; gap: 6px; margin-top: 9px; flex-wrap: wrap; }

/* status bar */
.status-bar { position: relative; display: flex; align-items: center; gap: 16px; padding: 0 14px; border-top: 1px solid rgba(255,255,255,.07); background: #21242a; }
.status-copy { min-width: 0; flex: 1; display: flex; align-items: center; gap: 9px; }
.status-copy > i { flex: 0 0 7px; width: 7px; height: 7px; border-radius: 50%; background: #6db07f; box-shadow: 0 0 8px rgba(109,176,127,.2); }
.status-copy > div { display: flex; flex-direction: column; gap: 2px; }
.status-copy strong { font-size: 12px; font-weight: 600; } .status-copy span { color: #9da1a8; font-size: 11px; }
.status-actions { display: flex; gap: 6px; } .status-actions .start { min-width: 150px; }

/* pills */
.pill { display: inline-flex; align-items: center; gap: 4px; padding: 2px 7px; border-radius: 999px; border: 1px solid rgba(255,255,255,.09); background: rgba(22,24,28,.78); color: #ccd0d8; font-size: 10px; letter-spacing: .2px; white-space: nowrap; line-height: 1.4; }
.pill.labs { color: #b9d0ff; border-color: rgba(78,140,255,.35); background: rgba(32,53,91,.76); }
.pill.best { color: #e8c47e; border-color: rgba(232,196,126,.35); background: rgba(232,196,126,.1); }
.pill.quick { color: #9dc0fd; border-color: rgba(78,140,255,.35); background: rgba(78,140,255,.12); }
.pill.ok { color: #8fd09e; border-color: rgba(109,176,127,.24); background: rgba(109,176,127,.09); }
.pill.warn { color: #efbd66; border-color: rgba(222,153,53,.3); background: rgba(222,153,53,.12); }
.pill.bad { color: #ffacae; border-color: rgba(229,72,77,.3); background: rgba(229,72,77,.1); }

/* library sheet */
.backdrop { position: absolute; inset: 0; background: rgba(5,6,8,.72); backdrop-filter: blur(4px); }
.sheet { position: absolute; width: 1080px; height: 700px; display: grid; grid-template-rows: 56px 1fr; overflow: hidden; border: 1px solid rgba(255,255,255,.12); border-radius: 12px; background: #202329; box-shadow: 0 24px 80px rgba(0,0,0,.65); }
.sheet.standalone { position: static; border-radius: 0; border: 0; box-shadow: none; }
.sheet-head { display: flex; align-items: center; gap: 12px; padding: 0 16px; border-bottom: 1px solid rgba(255,255,255,.07); }
.sheet-head h2 { font-size: 16px; font-weight: 600; }
.sheet-head .right { margin-left: auto; display: flex; align-items: center; gap: 14px; color: #9da1a8; font-size: 11px; }
.search { display: flex; align-items: center; gap: 8px; width: 240px; height: 30px; padding: 0 10px; border: 1px solid rgba(255,255,255,.09); border-radius: 6px; background: #16181c; color: #7a8089; font-size: 12px; }
.close { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 6px; color: #9da1a8; }
.sheet-body { display: grid; grid-template-columns: 200px 1fr 344px; min-height: 0; }
.rail { padding: 10px 8px; border-right: 1px solid rgba(255,255,255,.06); }
.rail-h { padding: 8px 10px 4px; color: #7a8089; font-size: 10px; font-weight: 650; letter-spacing: .6px; }
.rail-item { display: flex; align-items: center; justify-content: space-between; height: 30px; padding: 0 10px; border-radius: 6px; color: #9da1a8; font-size: 12px; }
.rail-item.active { background: rgba(78,140,255,.18); color: #f0f1f4; } .rail-item small { color: #7a8089; font-size: 11px; }
.rail-sep { height: 1px; margin: 8px 10px; background: rgba(255,255,255,.06); }
.list { min-width: 0; display: flex; flex-direction: column; border-right: 1px solid rgba(255,255,255,.06); }
.list-head { display: flex; align-items: center; justify-content: space-between; flex: 0 0 40px; padding: 0 16px; color: #9da1a8; font-size: 11px; font-weight: 650; letter-spacing: .75px; border-bottom: 1px solid rgba(255,255,255,.055); }
.list-head span { font-weight: 500; letter-spacing: 0; }
.row { position: relative; display: grid; grid-template-columns: 1fr 124px 96px; gap: 12px; align-items: center; min-height: 68px; padding: 10px 16px 10px 18px; border-bottom: 1px solid rgba(255,255,255,.045); }
.row.selected { background: rgba(78,140,255,.12); }
.row.selected::before { content: ""; position: absolute; left: 0; top: 10px; bottom: 10px; width: 3px; border-radius: 2px; background: linear-gradient(#6a9efe,#4e8cff); }
.row .name { font-size: 13px; font-weight: 550; } .row .role { margin-top: 2px; color: #9da1a8; font-size: 11px; }
.row .pills { display: flex; gap: 5px; margin-top: 6px; flex-wrap: wrap; }
.meters { display: flex; flex-direction: column; gap: 5px; }
.meter { display: grid; grid-template-columns: 42px 1fr; align-items: center; gap: 6px; color: #7a8089; font-size: 10px; }
.dots { display: flex; gap: 3px; } .dots i { width: 7px; height: 7px; border-radius: 50%; background: rgba(255,255,255,.12); } .dots i.f { background: #c8cbd1; }
.row .right { text-align: right; font-size: 11px; color: #9da1a8; }
.row .right b { display: block; font-size: 12px; font-weight: 550; color: #f0f1f4; } .row .right .ok { color: #91cca1; }
.row.installed-row { grid-template-columns: 1fr 150px 90px 74px; }
.row .muted { color: #9da1a8; font-size: 11px; }
.detail { min-width: 0; overflow: hidden; padding: 16px; background: #1b1d22; display: flex; flex-direction: column; gap: 12px; }
.detail h3 { font-size: 15px; font-weight: 600; } .detail .role { color: #9da1a8; font-size: 11px; margin-top: 2px; }
.detail .pills { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 8px; }
.compare { position: relative; width: 100%; aspect-ratio: 2 / 1; overflow: hidden; border-radius: 6px; border: 1px solid rgba(255,255,255,.08); background: #0c0d10; }
.compare img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; }
.compare .after { clip-path: inset(0 0 0 50%); }
.compare .line { position: absolute; top: 0; bottom: 0; left: 50%; width: 2px; transform: translateX(-1px); background: #f5f6f8; box-shadow: -1px 0 rgba(0,0,0,.4),1px 0 rgba(0,0,0,.4); }
.compare .knob { position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); display: grid; place-items: center; width: 28px; height: 28px; border: 1px solid rgba(255,255,255,.24); border-radius: 50%; background: rgba(27,29,34,.97); box-shadow: 0 4px 14px rgba(0,0,0,.42); color: #d9dce2; }
.compare .lab { position: absolute; bottom: 6px; padding: 2px 6px; border-radius: 10px; background: rgba(22,24,28,.78); border: 1px solid rgba(255,255,255,.09); color: #ccd0d8; font-size: 10px; }
.compare .lab.l { left: 6px; } .compare .lab.r { right: 6px; }
.caption { color: #7a8089; font-size: 10px; margin-top: -6px; }
.facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; overflow: hidden; border: 1px solid rgba(255,255,255,.08); border-radius: 8px; background: rgba(255,255,255,.08); }
.facts > div { padding: 8px 10px; background: rgba(18,21,27,.9); }
.facts dt { color: #9da1a8; font-size: 9px; text-transform: uppercase; letter-spacing: .45px; }
.facts dd { margin-top: 3px; font-size: 11px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.kv { border: 1px solid rgba(255,255,255,.08); border-radius: 8px; background: #16181c; padding: 4px 10px; }
.kv div { display: grid; grid-template-columns: 96px 1fr; gap: 10px; padding: 5px 0; border-bottom: 1px solid rgba(255,255,255,.04); }
.kv div:last-child { border: 0; } .kv dt { color: #9da1a8; font-size: 11px; }
.kv dd { font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: right; display: flex; justify-content: flex-end; gap: 4px; align-items: center; }
.fit { display: flex; align-items: center; gap: 8px; color: #9da1a8; font-size: 11px; }
.fit i { width: 7px; height: 7px; border-radius: 50%; background: #6db07f; box-shadow: 0 0 8px rgba(109,176,127,.2); } .fit i.warn { background: #de9935; box-shadow: none; }
.actions { display: flex; flex-direction: column; gap: 6px; margin-top: auto; }
.actions .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.actions .button { justify-content: center; }
.actions .link { text-align: center; font-size: 11px; margin-top: 2px; }

/* wireframes */
.wire { font-family: "Patrick Hand", "Comic Sans MS", cursive; color: #2b2d31; background: #f6f5f1; }
.wire h2 { font-size: 22px; font-weight: 400; }
.wire p { font-size: 15px; line-height: 1.35; }
.wire .win { position: relative; height: 180px; border: 2px solid #2b2d31; border-radius: 6px; background: #fff; display: grid; grid-template-columns: 22% 1fr 26%; overflow: hidden; }
.wire .win > div { border-right: 2px dashed #b8b6ae; padding: 8px; font-size: 13px; color: #6b6e75; }
.wire .win > div:last-child { border-right: 0; }
.wire .block { height: 10px; background: #dcdad2; border-radius: 3px; margin: 6px 0; }
.wire .img { position: absolute; inset: 30px 12px 14px; background: repeating-linear-gradient(45deg,#e7e5dd 0 8px,#f1efe8 8px 16px); border: 2px dashed #b8b6ae; }
.wire .overlay { position: absolute; left: 16%; right: 16%; top: 14%; bottom: 14%; border: 3px solid #b33d26; background: rgba(255,255,255,.92); display: grid; place-items: center; font-size: 15px; color: #b33d26; }
.wire .plusminus { display: flex; flex-direction: column; gap: 4px; margin-top: 10px; }
.wire .plusminus span { display: grid; grid-template-columns: 18px 1fr; gap: 6px; font-size: 14px; }
.wire .plusminus b { font-weight: 400; color: #3a7d44; } .wire .plusminus em { font-style: normal; color: #b33d26; }
.wire .tag { display: inline-block; padding: 2px 8px; border: 2px solid #2b2d31; border-radius: 999px; font-size: 13px; margin-left: 8px; }
"""


# --------------------------------------------------------------------------- icons
def icon(name: str, size: int = 14) -> str:
    paths = {
        "chevron": '<path d="m6 9 6 6 6-6"/>',
        "plus": '<path d="M12 5v14M5 12h14"/>',
        "minus": '<path d="M5 12h14"/>',
        "check": '<path d="m5 12 5 5L20 7"/>',
        "close": '<path d="M18 6 6 18M6 6l12 12"/>',
        "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
        "ext": '<path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
        "arrows": '<path d="m9 8-4 4 4 4M15 8l4 4-4 4"/>',
        "download": '<path d="M12 4v11M7 10l5 5 5-5M4 20h16"/>',
        "file": '<path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/>',
        "clock": '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
        "alert": '<path d="M12 3 2 21h20z"/><path d="M12 10v4M12 17.5v.5"/>',
    }
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths[name]}</svg>'
    )


# --------------------------------------------------------------------------- shell pieces
def toolbar() -> str:
    return (
        '<header class="toolbar">'
        '<div class="brand-mark"><i></i><i></i><i></i></div>'
        '<div class="toolbar-actions">'
        '<button class="button compact benchmark-shortcut">Run Benchmark</button>'
        f'<button class="button primary compact">{icon("plus", 13)} Add Media</button>'
        "</div></header>"
    )


def media_pane() -> str:
    rows = [
        ("selected", '<img src="thumb.jpg" alt="">', "unite_marseille.jpg", "1600 × 800 · 1.3 MP"),
        ("", '<img src="thumb.jpg" alt="" style="filter: hue-rotate(150deg) saturate(.6) brightness(.9);">', "DSCF0342.jpg", "6000 × 4000 · 24.0 MP"),
        ("", "<span>IMG</span>", "scan_1978_02.tif", "Inspecting…"),
    ]
    items = "".join(
        f'<div class="media-row {cls}"><div class="media-select"><div class="thumb">{thumb}</div>'
        f'<div class="media-copy"><strong>{name}</strong><span>{meta}</span></div></div>'
        f'<div class="remove">{icon("close", 16)}</div></div>'
        for cls, thumb, name, meta in rows
    )
    return (
        '<aside class="media-pane pane">'
        '<div class="pane-heading"><h1>Media</h1><span>3 items</span></div>'
        '<div class="segmented"><button class="active">Single</button><button>Batch</button></div>'
        '<p class="mode-scope">Single mode processes the selected item when you press Start.</p>'
        f'<div class="media-list">{items}</div>'
        '<div class="media-actions"><button class="button">Add More…</button><button class="button">Add Folder…</button>'
        '<button class="button danger ghost">Clear</button></div>'
        "</aside>"
    )


def preview_pane() -> str:
    return (
        '<section class="preview-pane">'
        '<div class="preview-header"><div><strong>unite_marseille.jpg</strong><span>1600 × 800</span></div></div>'
        '<div class="canvas-well">'
        '<div class="image-stage" style="width: 780px; height: 390px;"><img src="preview.jpg" alt="Selected photograph" style="width: 780px; height: 390px;"></div>'
        f'<div class="zoom-hud"><button>{icon("minus", 12)}</button><span>49%</span><button>{icon("plus", 12)}</button>'
        '<button class="active">Fit</button><button>1:1</button></div>'
        "</div></section>"
    )


def status_bar(title: str, detail: str, start: str, *, disabled: bool = False, working: bool = False) -> str:
    dot_style = ' style="background: #4e8cff; box-shadow: none;"' if working else ""
    cls = "button primary start" + (" disabled" if disabled else "")
    return (
        '<footer class="status-bar">'
        f'<div class="status-copy"><i{dot_style}></i><div><strong>{title}</strong><span>{detail}</span></div></div>'
        f'<div class="status-actions"><button class="{cls}">{start}</button></div>'
        "</footer>"
    )


# --------------------------------------------------------------------------- enhance pane sections
def task_section(active: str = "upscale") -> str:
    def btn(key: str, label: str, sub: str, extra: str = "") -> str:
        cls = " ".join(filter(None, [extra, "active" if active == key else ""]))
        return f'<button class="{cls}"><b>{label}</b><span>{sub}</span></button>'

    return (
        '<section class="control-section"><div class="eyebrow">TASK</div><div class="task-grid">'
        + btn("upscale", "Upscale", "Photos and artwork")
        + btn("restore", "Restore", "Noise, blur, JPEG")
        + btn("video", "Upscale Video", "Local SDR video", "video-task")
        + "</div></section>"
    )


def quality_section(active: str | None, quick_sub: str, best_sub: str, *, recipes: bool = True) -> str:
    q = "quick active" if active == "quick" else "quick"
    b = "best active" if active == "best" else "best"
    saved = (
        f'<div class="save-recipe">{icon("plus", 12)} Save current setup as recipe</div>' if recipes else ""
    )
    return (
        '<section class="control-section"><div class="eyebrow">QUALITY</div><div class="recipe-grid">'
        f'<button class="{q}"><b>Quick</b><span>{quick_sub}</span></button>'
        f'<button class="{b}"><b>Best</b><span>{best_sub}</span></button>'
        f"</div>{saved}</section>"
    )


def image_section(content: str = "photo", fixes: tuple[str, ...] = ()) -> str:
    seg = (
        '<div class="seg-inline">'
        f'<button class="{"active" if content == "photo" else ""}">Photo</button>'
        f'<button class="{"active" if content == "illustration" else ""}">Illustration</button>'
        "</div>"
    )
    chips = "".join(
        f'<span class="chip {"on" if key in fixes else ""}">{icon("check", 12) if key in fixes else ""}{label}</span>'
        for key, label in (("noise", "Noise"), ("jpeg", "JPEG artifacts"), ("blur", "Blur"), ("faces", "Faces"))
    )
    return (
        '<section class="control-section"><div class="eyebrow">YOUR IMAGE</div>'
        f'<span class="field-label">Content</span>{seg}'
        f'<span class="field-label">Fix first</span><div class="chips">{chips}</div>'
        "</section>"
    )


def plan_row(stage: str, name: str, meta: str, state_html: str, extra: str = "") -> str:
    return (
        f'<div class="plan-row"><span class="stage">{stage}</span>'
        f'<div><div class="name">{name}</div><div class="meta">{meta}</div></div>'
        f'<span class="state-slot">{state_html}</span>{extra}</div>'
    )


def state(kind: str, text: str) -> str:
    icons = {"ok": "check", "dl": "download", "warn": "alert", "file": "file", "busy": "clock"}
    cls = {"ok": "state ok", "warn": "state warn", "file": "state warn"}.get(kind, "state")
    return f'<span class="{cls}">{icon(icons[kind], 12)}{text}</span>'


def model_section(rows: str, note: str = "", *, eyebrow: str = "MODEL", right: str = "Change…", before: str = "") -> str:
    right_html = f"<a>{right}</a>" if right else ""
    note_html = f'<p class="model-note">{note}</p>' if note else ""
    return (
        f'<section class="control-section">{before}<div class="eyebrow"><span style="font-weight: 650; letter-spacing: .75px;">{eyebrow}</span>{right_html}</div>'
        f'<div class="plan">{rows}</div>{note_html}</section>'
    )


def advanced_section(sub: str = "Output, hardware, model library") -> str:
    return (
        '<section class="control-section"><div class="disclosure"><span>Advanced</span>'
        f'<small>{sub}</small><b>{icon("chevron", 14)}</b></div></section>'
    )


def summary_section(inp: str, out: str, model: str, device: str) -> str:
    return (
        '<section class="control-section summary-card"><div class="eyebrow">SUMMARY</div><dl>'
        f"<div><dt>Input</dt><dd>{inp}</dd></div><div><dt>Output</dt><dd>{out}</dd></div>"
        f"<div><dt>Model</dt><dd>{model}</dd></div><div><dt>Device</dt><dd>{device}</dd></div>"
        "</dl></section>"
    )


def about_block() -> str:
    return (
        '<section class="about-block"><div><b>LocalSR</b><span>0.0.13-alpha</span></div>'
        '<div class="links"><a>Performance</a><a>Copy diagnostics</a><a>System integrations</a></div>'
        "<p>Local processing · no media uploads<br>Models retain their own licenses.</p></section>"
    )


def enhance_pane(subtitle: str, body: str, *, style: str = "") -> str:
    # Outside the app grid the pane needs a definite height, or the flex column
    # (.inspector-scroll has flex-basis 0 and min-height 0) collapses to nothing.
    style = f' style="{style}"' if style else ""
    return (
        f'<aside class="enhance-pane pane"{style}>'
        f'<div class="pane-heading"><h1>Enhance</h1><span>{subtitle}</span></div>'
        f'<div class="inspector-scroll">{body}</div></aside>'
    )


def shell(enhance_html: str, status_html: str, overlay: str = "") -> str:
    return (
        '<div class="app-shell" style="width: 1440px; height: 900px;">'
        + toolbar()
        + '<main class="workspace">'
        + media_pane()
        + preview_pane()
        + enhance_html
        + "</main>"
        + status_html
        + overlay
        + "</div>"
    )


def page(root: str, extra_css: str = "") -> str:
    return (
        "<!doctype html>\n<html>\n<head>\n  <meta charset=\"utf-8\">\n  <script src=\"./support.js\"></script>\n</head>\n<body>\n<x-dc>\n<helmet>\n"
        f"  {FONT}\n  <style>{CSS}{extra_css}</style>\n</helmet>\n{root}\n</x-dc>\n</body>\n</html>\n"
    )


# --------------------------------------------------------------------------- shared rows
BEST_PHOTO = MODELS["realplksr_nomoswebphoto_x4"]
QUICK_PHOTO = MODELS["span_photo_x4"]
FBCNN = MODELS["fbcnn_color"]
HATL = MODELS["hat_l_x4_imagenet"]
HATS = MODELS["hat_s_x4"]
FACE_L = MODELS["hat_l_x4_face"] if "hat_l_x4_face" in MODELS else None

ROW_BEST_INSTALLED = plan_row(
    "Upscale ×4", "RealPLKSR ×4 NomosWebPhoto", f"CC BY 4.0 · {mb('realplksr_nomoswebphoto_x4')} · runs well here", state("ok", "On this Mac")
)
ROW_FBCNN_DL = plan_row(
    "Fix JPEG", "FBCNN Color", f"Apache-2.0 · {mb('fbcnn_color')} · runs well here", state("dl", "Download")
)


# --------------------------------------------------------------------------- artboards
def main_artboard() -> str:
    body = (
        task_section("upscale")
        + quality_section("best", "SPAN ×4 · about 4 s", "RealPLKSR ×4 · about 25 s")
        + image_section("photo", ("jpeg",))
        + model_section(
            ROW_FBCNN_DL + ROW_BEST_INSTALLED,
            f"Best for photos on this Mac. The JPEG stage needs one download ({mb('fbcnn_color')}); it happens when you press Start.",
        )
        + advanced_section()
        + summary_section("1600 × 800", "6400 × 3200", "FBCNN → RealPLKSR ×4", "Apple M2 · MPS")
        + about_block()
    )
    status = status_bar(
        "Ready · Best for photos",
        f"FBCNN Color downloads once ({mb('fbcnn_color')}), then the job starts.",
        f"Download {mb('fbcnn_color')}, then upscale",
    )
    return page(shell(enhance_pane("Best · Photo", body), status))


def dots(filled: int, total: int = 4) -> str:
    return '<span class="dots">' + "".join('<i class="f"></i>' if i < filled else "<i></i>" for i in range(total)) + "</span>"


def meters(q: int, s: int) -> str:
    return f'<div class="meters"><div class="meter">Quality{dots(q)}</div><div class="meter">Speed{dots(s)}</div></div>'


def lib_row(name: str, role: str, pills: str, q: int, s: int, size: str, status_html: str, selected: bool = False) -> str:
    return (
        f'<div class="row {"selected" if selected else ""}"><div><div class="name">{name}</div><div class="role">{role}</div>'
        f'<div class="pills">{pills}</div></div>{meters(q, s)}<div class="right"><b>{size}</b>{status_html}</div></div>'
    )


def pill(text: str, kind: str = "") -> str:
    return f'<span class="pill {kind}">{text}</span>'


def rail(active: str) -> str:
    def item(key: str, label: str, count: str) -> str:
        return f'<div class="rail-item {"active" if key == active else ""}"><span>{label}</span><small>{count}</small></div>'

    return (
        '<nav class="rail">'
        + item("job", "For this job", "4")
        + '<div class="rail-sep"></div><div class="rail-h">BROWSE</div>'
        + item("photos", "Upscale photos", "5")
        + item("illu", "Illustration", "1")
        + item("faces", "Faces", "2")
        + item("fix", "Fix noise, blur, JPEG", "4")
        + item("video", "Video (Labs)", "2")
        + '<div class="rail-sep"></div><div class="rail-h">ON THIS MAC</div>'
        + item("installed", "Installed", "5 · 1.06 GB")
        + item("own", "Your own files", "0")
        + "</nav>"
    )


def sheet_head(context_pill: str) -> str:
    return (
        '<div class="sheet-head"><h2>Model library</h2>'
        + (pill(context_pill, "quick") if context_pill else "")
        + f'<div class="search">{icon("search", 13)} Search models</div>'
        + '<div class="right"><span>Catalog 12 Sep 2026 · 13 models</span><a>Check for updates</a>'
        + f'<span class="close">{icon("close", 16)}</span></div></div>'
    )


def library_sheet(active_rail: str, list_html: str, detail_html: str, context_pill: str, *, standalone: bool = False) -> str:
    cls = "sheet standalone" if standalone else "sheet"
    pos = "" if standalone else ' style="left: 180px; top: 100px;"'
    return (
        f'<div class="{cls}"{pos}>{sheet_head(context_pill)}<div class="sheet-body">{rail(active_rail)}'
        f'<section class="list">{list_html}</section><aside class="detail">{detail_html}</aside></div></div>'
    )


def library_artboard() -> str:
    rows = (
        '<div class="list-head">FOR THIS JOB<span>Upscale · Photo · ×4 · sorted by quality</span></div>'
        + lib_row("RealPLKSR ×4 NomosWebPhoto", "Best photo detail; trained on real-world JPEG damage",
                  pill("Best", "best") + pill("CC BY 4.0") + pill("Runs well", "ok"), 4, 2, mb("realplksr_nomoswebphoto_x4"), '<span class="ok">Installed</span>')
        + lib_row("SPAN ×4 NomosUni", "Fast everyday photos; seconds even on CPU",
                  pill("Quick", "quick") + pill("CC BY 4.0") + pill("Runs well", "ok"), 1, 3, mb("span_photo_x4"), '<span class="ok">Installed</span>')
        + lib_row("HAT-L ×4 ImageNet", "Highest fidelity on clean sources; slow",
                  pill("Apache-2.0") + pill("Runs well", "ok"), 3, 1, mb("hat_l_x4_imagenet"), "<span>Not downloaded</span>", selected=True)
        + lib_row("HAT-S ×4", "Lighter HAT for laptops and small GPUs",
                  pill("Apache-2.0") + pill("Runs well", "ok"), 2, 2, mb("hat_s_x4"), "<span>Not downloaded</span>")
    )
    detail = (
        '<div><h3>HAT-L ×4 ImageNet</h3><div class="role">Highest fidelity on clean sources · slow</div>'
        f'<div class="pills">{pill("Apache-2.0")}{pill("Runs well", "ok")}</div></div>'
        '<div class="compare"><img src="crop-before.jpg" alt="Original detail"><img class="after" src="crop-after.jpg" alt="Upscaled detail">'
        f'<span class="line"></span><span class="knob">{icon("arrows", 14)}</span><span class="lab l">Original</span><span class="lab r">HAT-L ×4</span></div>'
        '<p class="caption">Detail from a 480 × 240 test input. A sample, not a guarantee.</p>'
        '<dl class="facts"><div><dt>Scale</dt><dd>×4</dd></div><div><dt>Download</dt><dd>' + mb("hat_l_x4_imagenet") + "</dd></div>"
        '<div><dt>Memory while running</dt><dd>about 6 GB</dd></div><div><dt>Speed</dt><dd>Slow</dd></div>'
        '<div><dt>Architecture</dt><dd>HAT · Spandrel</dd></div><div><dt>Training</dt><dd>ImageNet, bicubic</dd></div></dl>'
        '<div class="fit"><i></i>Runs well · Apple M2 · 16 GB shared memory</div>'
        '<dl class="kv">'
        f'<div><dt>License</dt><dd>Apache-2.0 {icon("ext", 11)}</dd></div>'
        f'<div><dt>Author</dt><dd>{HATL["author"]}</dd></div>'
        f'<div><dt>Source</dt><dd>github.com/XPixelGroup/HAT {icon("ext", 11)}</dd></div>'
        f'<div><dt>File</dt><dd>{HATL["filename"]}</dd></div>'
        '<div><dt>Integrity</dt><dd>SHA-256 checked after download</dd></div>'
        '<div><dt>Commercial use</dt><dd>Allowed · attribution kept</dd></div></dl>'
        '<div class="actions">'
        f'<button class="button primary">{icon("download", 13)} Download {mb("hat_l_x4_imagenet")}</button>'
        '<div class="pair"><button class="button">Use for this job</button><button class="button">Make my Best · Photo</button></div>'
        '<a class="link">Preview on a crop of your image · later</a></div>'
    )
    overlay = '<div class="backdrop"></div>' + library_sheet("job", rows, detail, "Choosing for Upscale · Best · Photo")
    body = (
        task_section("upscale")
        + quality_section("best", "SPAN ×4 · about 4 s", "RealPLKSR ×4 · about 25 s")
        + image_section("photo", ())
        + model_section(ROW_BEST_INSTALLED, "Best for photos on this Mac.")
        + advanced_section()
        + summary_section("1600 × 800", "6400 × 3200", "RealPLKSR ×4", "Apple M2 · MPS")
        + about_block()
    )
    status = status_bar("Ready · Best for photos", "Everything this job needs is on this Mac.", "Upscale selected")
    return page(shell(enhance_pane("Best · Photo", body), status, overlay))


def installed_artboard() -> str:
    def irow(name: str, used: str, size: str, last: str, selected: bool = False) -> str:
        return (
            f'<div class="row installed-row {"selected" if selected else ""}"><div><div class="name">{name}</div><div class="role">{used}</div></div>'
            f'<span class="muted">{last}</span><div class="right"><b>{size}</b><span class="ok">Verified</span></div>'
            '<button class="button compact ghost danger" style="justify-self: end;">Remove</button></div>'
        )

    rows = (
        '<div class="list-head">INSTALLED · 5 MODELS + DETECTOR · 1.06 GB<span>Reveal folder</span></div>'
        + irow("RealPLKSR ×4 NomosWebPhoto", "Used by Best · Photo", mb("realplksr_nomoswebphoto_x4"), "Used today")
        + irow("SPAN ×4 NomosUni", "Used by Quick · Photo", mb("span_photo_x4"), "Used today")
        + irow("FBCNN Color", "Fix JPEG", mb("fbcnn_color"), "Used yesterday")
        + irow("NAFNet GoPro Deblur", "Fix Blur", mb("nafnet_gopro_deblur"), "Used 3 Sep")
        + irow("NAFNet SIDD Width64", "Used by Best · Restore", mb("nafnet_sidd_width64"), "Used 21 Aug", selected=True)
        + irow("YuNet face detector", "Faces · detector (MIT)", "0.2 MB", "—")
    )
    detail = (
        '<div><h3>NAFNet SIDD Width64</h3><div class="role">Camera-noise removal · highest fidelity</div>'
        f'<div class="pills">{pill("Best · Restore", "best")}{pill("MIT")}{pill("Runs well", "ok")}</div></div>'
        '<dl class="kv">'
        '<div><dt>Installed</dt><dd>21 Aug 2026</dd></div>'
        f'<div><dt>Size on disk</dt><dd>{mb("nafnet_sidd_width64")}</dd></div>'
        '<div><dt>Integrity</dt><dd>SHA-256 matches the catalog</dd></div>'
        f'<div><dt>File</dt><dd>{MODELS["nafnet_sidd_width64"]["filename"]}</dd></div>'
        '<div><dt>Last used</dt><dd>21 Aug 2026</dd></div></dl>'
        '<p class="inline-warning">Best · Restore uses this model. Removing it means Best needs a '
        f'{mb("nafnet_sidd_width64")} download again the next time you choose it.</p>'
        '<div class="actions"><button class="button danger">Remove from this Mac</button>'
        '<div class="pair"><button class="button">Reveal in Finder</button><button class="button">Re-verify</button></div></div>'
    )
    root = (
        '<div style="width: 1080px; height: 700px; background: #202329;">'
        + library_sheet("installed", rows, detail, "", standalone=True)
        + "</div>"
    )
    return page(root)


def pane_artboard(subtitle: str, body: str) -> str:
    root = (
        '<div style="width: 340px; height: 1000px; background: #1b1d22;">'
        + enhance_pane(subtitle, body, style="width: 340px; height: 1000px;")
        + "</div>"
    )
    return page(root)


def first_run_artboard() -> str:
    get_started = (
        '<section class="control-section"><div class="eyebrow">GET STARTED</div>'
        '<div class="notice"><b>Models aren’t bundled</b><p>LocalSR downloads the models you pick once, checks them, and keeps them on this Mac. Your images never leave the computer.</p>'
        f'<div class="actions-row"><button class="button primary compact">Get Quick · {mb("span_photo_x4")}</button>'
        f'<button class="button compact">Quick + Best · 34 MB</button></div></div></section>'
    )
    body = (
        task_section("upscale")
        + get_started
        + quality_section("quick", f"SPAN ×4 · {mb('span_photo_x4')} download", f"RealPLKSR ×4 · {mb('realplksr_nomoswebphoto_x4')} download", recipes=False)
        + image_section("photo", ())
        + model_section(
            plan_row("Upscale ×4", "SPAN ×4 NomosUni", f"CC BY 4.0 · {mb('span_photo_x4')} · runs well here", state("dl", "Download")),
            "Downloads happen when you press Start, or now with the buttons above.",
        )
        + advanced_section()
        + summary_section("1600 × 800", "6400 × 3200", "SPAN ×4", "Apple M2 · MPS")
    )
    return pane_artboard("Quick · Photo", body)


def downloading_artboard() -> str:
    row = plan_row(
        "Upscale ×4", "RealPLKSR ×4 NomosWebPhoto", "CC BY 4.0 · runs well here", state("busy", "42%"),
        '<div class="full-span"><span class="download-track"><i style="width: 42%;"></i></span>'
        '<div class="meta" style="display: flex; justify-content: space-between;"><span>12.6 of 30 MB · 1.4 MB/s · about 12 s left</span><a>Cancel</a></div></div>',
    )
    body = (
        task_section("upscale")
        + quality_section("best", "SPAN ×4 · about 4 s", "RealPLKSR ×4 · about 25 s")
        + image_section("photo", ())
        + model_section(row, "The file’s SHA-256 is checked before it is ever loaded. The job starts by itself when the download finishes.")
        + advanced_section()
        + summary_section("1600 × 800", "6400 × 3200", "RealPLKSR ×4", "Apple M2 · MPS")
    )
    return pane_artboard("Downloading", body)


def illustration_artboard() -> str:
    row = plan_row(
        "Upscale ×4", "RealPLKSR ×4 NomosWebPhoto", f"Photo-trained · CC BY 4.0 · {mb('realplksr_nomoswebphoto_x4')}", state("ok", "On this Mac")
    )
    note = (
        '<p class="inline-warning">No fully verified illustration model yet. RealPLKSR ×4 HFA2k is available in Labs while its license is being confirmed — '
        "<a>use it for this job</a>.</p>"
    )
    body = (
        task_section("upscale")
        + quality_section("best", "SPAN ×4 · about 4 s", "RealPLKSR ×4 · about 25 s")
        + image_section("illustration", ())
        + model_section(row + "", "", right="Change…")
        .replace("</section>", note + "</section>")
        + advanced_section()
        + summary_section("1600 × 800", "6400 × 3200", "RealPLKSR ×4", "Apple M2 · MPS")
    )
    return pane_artboard("Best · Illustration", body)


def fit_artboard() -> str:
    row = plan_row("Upscale ×4", "HAT-L ×4 ImageNet", f"Apache-2.0 · {mb('hat_l_x4_imagenet')} · about 6 GB while running", state("warn", "Heavy here"))
    warning = (
        '<p class="inline-warning">Needs about 6 GB while running; this Mac has 8 GB of shared memory. LocalSR will use safe-memory mode and smaller tiles — expect roughly 3× longer.</p>'
        f'<p class="model-note"><a>Use HAT-S ×4 instead</a> · about 2 GB · {mb("hat_s_x4")}</p>'
    )
    body = (
        task_section("upscale")
        + quality_section(None, "SPAN ×4 · about 6 s", "RealPLKSR ×4 · about 40 s")
        + image_section("photo", ())
        + model_section(row, "", eyebrow="MODEL · CHOSEN BY YOU", right="Back to Best").replace("</section>", warning + "</section>")
        + advanced_section()
        + summary_section("1600 × 800", "6400 × 3200", "HAT-L ×4", "Apple M1 · 8 GB")
    )
    return pane_artboard("Custom · Photo", body)


def update_artboard() -> str:
    before = (
        '<div class="notice" style="margin-bottom: 12px;"><b>Catalog updated</b>'
        '<p>A newer model now qualifies for Best · Photo: DRCT-L ×4 (example values) · 110 MB. Your current Best stays until you switch.</p>'
        '<div class="actions-row"><button class="button compact">See in library</button><button class="button compact">Switch</button><button class="button compact ghost">Keep</button></div></div>'
    )
    body = (
        task_section("upscale")
        + quality_section("best", "SPAN ×4 · about 4 s", "RealPLKSR ×4 · about 25 s")
        + image_section("photo", ())
        + model_section(ROW_BEST_INSTALLED, "Best for photos on this Mac.", before=before)
        + advanced_section()
        + summary_section("1600 × 800", "6400 × 3200", "RealPLKSR ×4", "Apple M2 · MPS")
    )
    return pane_artboard("Best · Photo", body)


def faces_artboard() -> str:
    face_file = FACE_L["filename"] if FACE_L else "hat_l_x4_face_task4.pth"
    face_size = mb("hat_l_x4_face") if FACE_L else "166 MB"
    rows = ROW_BEST_INSTALLED + plan_row(
        "Faces", "HAT-L ×4 Face", f"Rights unresolved · {face_size} · LocalSR won’t download this", state("file", "Needs your file")
    ) + plan_row("Detector", "YuNet 2023mar", "MIT · 0.2 MB · downloads on first use", state("dl", "Download"))
    after = (
        f'<p class="model-note">Choose a copy of <span style="font-family: ui-monospace, monospace;">{face_file}</span> you are licensed to use. LocalSR checks its SHA-256 before loading it.</p>'
        '<button class="button full">Choose checkpoint…</button>'
        '<label class="terms"><span class="box"></span>I understand the checkpoint rights are unresolved and will provide a copy I am permitted to use.</label>'
    )
    body = (
        task_section("upscale")
        + quality_section("best", "SPAN ×4 · about 4 s", "RealPLKSR ×4 · about 25 s")
        + image_section("photo", ("faces",))
        + model_section(rows, "").replace("</section>", after + "</section>")
        + advanced_section()
        + summary_section("1600 × 800", "6400 × 3200", "RealPLKSR ×4 + Face", "Apple M2 · MPS")
    )
    return pane_artboard("Best · Photo · Faces", body)


def wireframes_artboard() -> str:
    def option(title: str, tag: str, win: str, plus: list[str], minus: list[str]) -> str:
        pm = "".join(f"<span><b>+</b>{p}</span>" for p in plus) + "".join(f"<span><em>−</em>{m}</span>" for m in minus)
        return (
            f'<div style="display: flex; flex-direction: column; gap: 10px;"><h2>{title}{tag}</h2>{win}'
            f'<div class="plusminus">{pm}</div></div>'
        )

    def mini(center: str, right: str, overlay: str = "") -> str:
        return (
            '<div class="win"><div>Media<div class="block"></div><div class="block" style="width: 70%;"></div><div class="block" style="width: 85%;"></div></div>'
            f"<div>{center}</div><div>{right}</div>{overlay}</div>"
        )

    a = mini(
        'Preview<div class="img"></div>', 'Enhance<div class="block"></div><div class="block"></div><div class="block" style="width: 60%;"></div>',
        '<div class="overlay">Library sheet<br>rail · list · detail</div>',
    )
    b = mini(
        '← Back &nbsp; Library page<div class="block" style="margin-top: 14px;"></div><div class="block" style="width: 80%;"></div><div class="block" style="width: 90%;"></div><div class="block" style="width: 60%;"></div>',
        'Enhance<div class="block"></div><div class="block"></div>',
    )
    c = mini(
        'Preview<div class="img"></div>',
        'Enhance<div class="block"></div><div class="block" style="background: #f3c9c0;"></div><div class="block" style="background: #f3c9c0;"></div><div class="block" style="background: #f3c9c0;"></div><div class="block" style="background: #f3c9c0;"></div><div class="block" style="background: #f3c9c0;"></div><div class="block" style="background: #f3c9c0;"></div>',
    )
    root = (
        '<div class="wire" style="width: 1200px; height: 560px; padding: 28px 32px;">'
        '<p style="margin-bottom: 18px;">Where should the library live? Three shapes considered; A is the direction.</p>'
        '<div style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 28px;">'
        + option("A · Sheet over the workspace", '<span class="tag">chosen</span>', a,
                 ["Your image and the job stay visible behind it", "Same component becomes a full page below 920 px", "Room for samples, licenses, provenance"],
                 ["One more layer to dismiss"])
        + option("B · Library as a workspace page", "", b,
                 ["Most room for large before/after samples"],
                 ["Leaves the job; the image disappears", "Needs its own navigation state"])
        + option("C · Expand inside the pane", "", c,
                 ["No new surface"],
                 ["340 px cannot hold samples, badges and licenses", "Scrolls forever as the catalog grows — the dropdown problem again"])
        + "</div></div>"
    )
    return page(root)


ARTBOARDS = {
    "Main": main_artboard,
    "Library": library_artboard,
    "PaneFirstRun": first_run_artboard,
    "PaneDownloading": downloading_artboard,
    "PaneIllustration": illustration_artboard,
    "PaneFit": fit_artboard,
    "PaneUpdate": update_artboard,
    "PaneFaces": faces_artboard,
    "Installed": installed_artboard,
    "Wireframes": wireframes_artboard,
}

CANVAS = {
    "artboards": [
        {"file": "Main.dc.html", "x": 0, "y": 0, "w": 1440, "h": 900, "title": "Enhance pane · default (Best · Photo · fix JPEG)"},
        {"file": "Library.dc.html", "x": 1540, "y": 0, "w": 1440, "h": 900, "title": "Model library · choosing for this job"},
        {"file": "PaneFirstRun.dc.html", "x": 0, "y": 1040, "w": 340, "h": 1000, "title": "First run · nothing installed"},
        {"file": "PaneDownloading.dc.html", "x": 430, "y": 1040, "w": 340, "h": 1000, "title": "Download in progress"},
        {"file": "PaneIllustration.dc.html", "x": 860, "y": 1040, "w": 340, "h": 1000, "title": "Illustration · honest fallback"},
        {"file": "PaneFit.dc.html", "x": 1290, "y": 1040, "w": 340, "h": 1000, "title": "Hardware fit · heavy model on 8 GB"},
        {"file": "PaneUpdate.dc.html", "x": 1720, "y": 1040, "w": 340, "h": 1000, "title": "Catalog update · offers, never switches"},
        {"file": "PaneFaces.dc.html", "x": 2150, "y": 1040, "w": 340, "h": 1000, "title": "Faces · bring your own checkpoint"},
        {"file": "Installed.dc.html", "x": 0, "y": 2180, "w": 1080, "h": 700, "title": "Library · Installed and storage"},
        {"file": "Wireframes.dc.html", "x": 1180, "y": 2180, "w": 1200, "h": 560, "title": "Where the library lives · options considered"},
    ],
    "annotations": [
        {"id": "note-main", "x": 0, "y": -270, "w": 540,
         "text": "Default state. Task → Quality → Your image → the resolved plan.\nThe two model dropdowns are gone; the MODEL card is the answer, and “Change…” opens the library filtered to this job. Start says what it will download first.\n\nAssumes two open decisions: the NomosWebPhoto license is confirmed as CC BY 4.0 (until then Best resolves to HAT-L ×4 and this model reads “License being confirmed”), and Denoise is renamed Restore. Strings show the macOS variant (“this Mac”)."},
        {"id": "note-library", "x": 1540, "y": -270, "w": 460,
         "text": "The library is a sheet over the workspace: a rail by what a model does, rows with quality, speed, licence and fit, and a detail column with a real before/after crop and provenance.\nOnly models LocalSR has verified are listed."},
        {"id": "note-states", "x": -500, "y": 1040, "w": 420,
         "text": "Pane states, left to right:\n· first run, nothing installed\n· a download in progress\n· Illustration with no verified model — honest fallback plus the Labs escape hatch\n· a heavy model on an 8 GB machine\n· a catalog update that offers and never switches\n· Faces with unresolved rights — bring your own file"},
        {"id": "note-installed", "x": -500, "y": 2180, "w": 420,
         "text": "Installed doubles as storage management: what each file is used by, size, last use, and Remove with a warning when a preset depends on it."},
    ],
    "launch": {"view": "canvas"},
}


def main() -> None:
    for name, fn in ARTBOARDS.items():
        (OUT / f"{name}.dc.html").write_text(fn(), encoding="utf-8")
    (OUT / "canvas.json").write_text(json.dumps(CANVAS, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(ARTBOARDS)} artboards + canvas.json to {OUT}")


if __name__ == "__main__":
    main()
