import AppKit
import Foundation
import UniformTypeIdentifiers

private let arguments = CommandLine.arguments
guard arguments.count == 3 else {
    FileHandle.standardError.write(Data("usage: LocalSRDialog MODE INITIAL_DIRECTORY\n".utf8))
    exit(2)
}

let mode = arguments[1]
let initialDirectory = arguments[2]
guard ["images", "model", "folder", "output"].contains(mode) else {
    FileHandle.standardError.write(Data("unsupported dialog mode\n".utf8))
    exit(2)
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
application.finishLaunching()

let panel = NSOpenPanel()
panel.directoryURL = URL(fileURLWithPath: initialDirectory, isDirectory: true)
panel.resolvesAliases = true
panel.canCreateDirectories = mode == "output"
panel.canChooseDirectories = mode == "folder" || mode == "output"
panel.canChooseFiles = !panel.canChooseDirectories
panel.allowsMultipleSelection = mode == "images"

switch mode {
case "images":
    panel.title = "Add Images"
    panel.message = "Choose one or more images to add to LocalSR."
    panel.allowedContentTypes = ["jpg", "jpeg", "png", "tif", "tiff", "webp", "dng"]
        .compactMap { UTType(filenameExtension: $0) }
case "model":
    panel.title = "Select Model Checkpoint"
    panel.message = "Only open model checkpoints from sources you trust."
    panel.allowedContentTypes = ["pth", "pt", "safetensors"]
        .compactMap { UTType(filenameExtension: $0) }
case "folder":
    panel.title = "Add Image Folder"
    panel.message = "Choose a folder whose supported images should be added."
default:
    panel.title = "Select Output Directory"
    panel.message = "Choose where LocalSR should save completed images."
}

application.activate(ignoringOtherApps: true)
guard panel.runModal() == .OK else {
    exit(0)
}

for url in panel.urls {
    print(url.path)
}
