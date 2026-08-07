import QtQuick

QtObject {
    property var modelRows: [
        {
            "id": "span_x4_official",
            "name": "SPAN ×4 — Ultra Fast",
            "description": "Lightweight everyday upscaling",
            "architecture": "SPAN",
            "size": "9 MB",
            "license": "Apache-2.0",
            "installed": false,
            "status": "Optional download"
        }
    ]
    property int modelIndex: 0
    property string modelStatus: "Not installed. Downloaded only when requested."
    property bool downloadVisible: false
    property int downloadProgress: 0
    property string downloadActionText: "Download 9 MB"
    property bool imageReady: false
    property string imageTitle: "Drop an image"
    property string imageSubtitle: "JPEG, PNG, TIFF, WebP or camera DNG"
    property string sourcePreviewSource: ""
    property string progressivePreviewSource: ""
    property string previewError: ""
    property real activeTileX: 0
    property real activeTileY: 0
    property real activeTileWidth: 0
    property real activeTileHeight: 0
    property bool tileActive: false
    property var deviceRows: [{"id": "mps", "name": "Apple GPU (Metal)", "detail": "8 GB available"}]
    property int deviceIndex: 0
    property var scaleRows: [{"label": "4×", "value": 4}]
    property int scaleIndex: 0
    property var formatRows: ["png", "jpg", "tif"]
    property int formatIndex: 0
    property var tileRows: ["64", "128", "256"]
    property int tileIndex: 1
    property var haloRows: ["8", "16", "32"]
    property int haloIndex: 1
    property var precisionRows: ["fp32", "fp16"]
    property int precisionIndex: 0
    property bool safeMemory: false
    property bool safeMemoryEditable: true
    property bool preserveMetadata: true
    property int jpegQuality: 96
    property string outputDirectory: "~/Pictures"
    property string predictedSize: "Choose an image"
    property string estimateText: "Time and memory appear after an image and model are selected."
    property string hardwareText: "Hardware detection runs in the isolated worker."
    property string memoryText: "Available RAM and device memory appear here."
    property string warningText: ""
    property string presetMessage: "Choose Quick or Best Quality, or tune settings yourself."
    property real progress: 0
    property string progressText: "Ready"
    property string liveResourceText: "Live memory will appear while a job is running."
    property bool canStart: false
    property bool canCancel: false
    property bool canOpenResult: false
    property real pressurePercent: 31
    property string pressureLabel: "Low pressure"
    property color pressureColor: "#48d597"
    property string mpsMemoryText: "Compressed 1.2 GB · swap 220 MB"

    function chooseImage() {}
    function setImageFromUrl(_value) {}
    function setModelIndex(_index) {}
    function downloadModel() {}
    function setDeviceIndex(_index) {}
    function setScaleIndex(_index) {}
    function setFormatIndex(_index) {}
    function setTileIndex(_index) {}
    function setHaloIndex(_index) {}
    function setPrecisionIndex(_index) {}
    function setSafeMemory(_enabled) {}
    function setPreserveMetadata(_enabled) {}
    function setJpegQuality(_value) {}
    function chooseOutputDirectory() {}
    function applyPreset(_name) {}
    function startUpscale() {}
    function cancelUpscale() {}
    function openResult() {}
    function revealResult() {}
    function refreshHardware() {}
    function shutdown() {}
}
