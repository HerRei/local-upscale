pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    DesignMock { id: designMock }
    property var localSR: designMock
    visible: true
    width: 1440
    height: 900
    minimumWidth: 1120
    minimumHeight: 700
    title: "LocalSR"
    color: "#090d13"

    property bool advancedOpen: false
    property bool showLiveResult: localSR.progress > 0
    readonly property color accent: "#7c6cff"
    readonly property color teal: "#3ed6c4"
    readonly property color muted: "#8b97aa"

    onClosing: localSR.shutdown()

    Rectangle {
        anchors.fill: parent
        color: "#090d13"

        Rectangle {
            width: 620
            height: 620
            radius: 310
            color: "#231d57"
            opacity: 0.22
            x: -260
            y: -330
        }
        Rectangle {
            width: 520
            height: 520
            radius: 260
            color: "#073f48"
            opacity: 0.13
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.rightMargin: -220
            anchors.bottomMargin: -260
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0



        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 14
            Layout.bottomMargin: 8
            spacing: 12

            ScrollView {
                Layout.preferredWidth: 318
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                contentWidth: availableWidth

                ColumnLayout {
                    width: parent.width
                    spacing: 12

                    Panel {
                        Layout.fillWidth: true
                        ColumnLayout {
                            width: parent.width
                            spacing: 10
                            RowLayout {
                                Layout.fillWidth: true
                                SectionLabel { text: "Source image" }
                                Item { Layout.fillWidth: true }
                                Text {
                                    text: localSR.imageReady ? "READY" : "STEP 1"
                                    color: localSR.imageReady ? teal : "#7f8da3"
                                    font.pixelSize: 9
                                    font.weight: Font.Bold
                                    font.letterSpacing: 0.8
                                }
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 92
                                radius: 12
                                color: dropArea.containsDrag ? "#24264a" : "#101722"
                                border.color: dropArea.containsDrag ? accent : "#2a3547"
                                border.width: dropArea.containsDrag ? 2 : 1
                                Column {
                                    anchors.centerIn: parent
                                    width: parent.width - 24
                                    spacing: 5
                                    Text {
                                        width: parent.width
                                        text: localSR.imageTitle
                                        color: "#e6edf7"
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                        horizontalAlignment: Text.AlignHCenter
                                        elide: Text.ElideMiddle
                                    }
                                    Text {
                                        width: parent.width
                                        text: localSR.imageSubtitle
                                        color: muted
                                        font.pixelSize: 10
                                        horizontalAlignment: Text.AlignHCenter
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        width: parent.width
                                        text: "Drop here or click to browse"
                                        color: "#6f7c91"
                                        font.pixelSize: 10
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: localSR.chooseImage()
                                }
                                DropArea {
                                    id: dropArea
                                    anchors.fill: parent
                                    onDropped: function(drop) {
                                        if (drop.hasUrls && drop.urls.length > 0)
                                            localSR.setImageFromUrl(drop.urls[0].toString())
                                    }
                                }
                            }
                            Text {
                                visible: localSR.previewError.length > 0
                                Layout.fillWidth: true
                                text: localSR.previewError
                                color: "#ff8899"
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    Text {
                        text: "AUTOMATIC"
                        color: "#69768b"
                        font.pixelSize: 9
                        font.weight: Font.Bold
                        font.letterSpacing: 1.2
                        Layout.leftMargin: 4
                    }

                    ModeCard {
                        Layout.fillWidth: true
                        title: "Quick"
                        subtitle: "Fast model, fastest safe accelerator and efficient precision."
                        badge: "⚡"
                        accent: teal
                        estimate: localSR.quickEstimate
                        onSelected: localSR.applyPreset("quick")
                    }

                    ModeCard {
                        Layout.fillWidth: true
                        title: "Best Quality"
                        subtitle: "Highest-quality compatible model with safe automatic tiling."
                        badge: "HQ"
                        accent: window.accent
                        estimate: localSR.bestEstimate
                        onSelected: localSR.applyPreset("best")
                    }

                    Text {
                        Layout.fillWidth: true
                        Layout.leftMargin: 4
                        Layout.rightMargin: 4
                        text: localSR.presetMessage
                        color: "#8996aa"
                        font.pixelSize: 10
                        wrapMode: Text.WordWrap
                    }

                    Panel {
                        Layout.fillWidth: true
                        ColumnLayout {
                            width: parent.width
                            spacing: 9
                            SectionLabel { text: "Model" }
                            ModernCombo {
                                Layout.fillWidth: true
                                model: localSR.modelRows
                                textRole: "name"
                                currentIndex: localSR.modelIndex
                                onActivated: function(index) { localSR.setModelIndex(index) }
                            }
                            Text {
                                Layout.fillWidth: true
                                text: localSR.modelStatus
                                color: "#8592a6"
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                                maximumLineCount: 4
                                elide: Text.ElideRight
                            }
                            ProgressBar {
                                id: downloadBar
                                visible: localSR.downloadVisible
                                Layout.fillWidth: true
                                value: localSR.downloadProgress / 100
                                background: Rectangle { radius: 3; color: "#222b39" }
                                contentItem: Item {
                                    implicitHeight: 6
                                    Rectangle {
                                        width: parent.width * downloadBar.visualPosition
                                        height: parent.height
                                        radius: 3
                                        color: teal
                                    }
                                }
                            }
                            ModernButton {
                                Layout.fillWidth: true
                                text: localSR.downloadActionText
                                onClicked: localSR.downloadModel()
                            }
                        }
                    }
                    Item { Layout.preferredHeight: 8 }
                }
            }

            Panel {
                id: canvasPanel
                Layout.fillWidth: true
                Layout.fillHeight: true
                padding: 10

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    Rectangle {
                        anchors.fill: parent
                        radius: 12
                        color: "#080b10"
                        border.color: "#202938"

                        Flickable {
                            id: flickable
                            anchors.fill: parent
                            anchors.margins: 2
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            contentWidth: imageContainer.width * imageContainer.scale
                            contentHeight: imageContainer.height * imageContainer.scale
                            leftMargin: Math.max(0, (width - contentWidth) / 2)
                            topMargin: Math.max(0, (height - contentHeight) / 2)
                            interactive: imageContainer.scale > 1.0

                            Item {
                                id: imageContainer
                                property real baseScale: 1.0
                                property real fitRatio: localSR.imageReady ? Math.min(flickable.width / Math.max(1, previewImage.implicitWidth), flickable.height / Math.max(1, previewImage.implicitHeight)) : 1.0
                                width: localSR.imageReady ? previewImage.implicitWidth * fitRatio : flickable.width
                                height: localSR.imageReady ? previewImage.implicitHeight * fitRatio : flickable.height
                                transformOrigin: Item.TopLeft
                                
                                Image {
                                    id: previewImage
                                    anchors.fill: parent
                                    source: localSR.sourcePreviewSource
                                    cache: false
                                    asynchronous: false
                                    fillMode: Image.Stretch
                                    mipmap: imageContainer.scale < 10.0
                                    smooth: imageContainer.scale < 10.0
                                    visible: localSR.imageReady
                                }

                                Item {
                                    id: resultClip
                                    anchors.fill: previewImage
                                    visible: window.showLiveResult && localSR.imageReady
                                    clip: true
                                    width: compareSlider.active ? compareSlider.x + (compareSlider.width / 2) : previewImage.width
                                    
                                    Image {
                                        id: resultImage
                                        width: previewImage.width
                                        height: previewImage.height
                                        source: localSR.progressivePreviewSource
                                        cache: false
                                        asynchronous: false
                                        fillMode: Image.Stretch
                                        mipmap: imageContainer.scale < 10.0
                                        smooth: imageContainer.scale < 10.0
                                    }
                                }
                                
                                Rectangle {
                                    id: compareSlider
                                    property bool active: !localSR.tileActive && window.showLiveResult && localSR.imageReady && localSR.progress >= 1.0
                                    visible: active
                                    width: Math.max(2, 4 / imageContainer.scale)
                                    height: previewImage.height
                                    x: previewImage.width / 2
                                    color: teal
                                    
                                    MouseArea {
                                        anchors.fill: parent
                                        anchors.margins: -15 / imageContainer.scale
                                        cursorShape: Qt.SizeHorCursor
                                        drag.target: compareSlider
                                        drag.axis: Drag.XAxis
                                        drag.minimumX: 0
                                        drag.maximumX: previewImage.width
                                    }
                                }

                                Rectangle {
                                    id: activeTileRect
                                    visible: localSR.tileActive && window.showLiveResult
                                    x: previewImage.x + (previewImage.width - previewImage.paintedWidth) / 2
                                        + localSR.activeTileX * previewImage.paintedWidth
                                    y: previewImage.y + (previewImage.height - previewImage.paintedHeight) / 2
                                        + localSR.activeTileY * previewImage.paintedHeight
                                    width: Math.max(2, localSR.activeTileWidth * previewImage.paintedWidth)
                                    height: Math.max(2, localSR.activeTileHeight * previewImage.paintedHeight)
                                    color: "transparent"
                                    border.color: teal
                                    border.width: 2
                                    radius: 3
                                    Rectangle {
                                        anchors.fill: parent
                                        color: teal
                                        opacity: 0.09
                                    }
                                    SequentialAnimation on opacity {
                                        running: activeTileRect.visible
                                        loops: Animation.Infinite
                                        NumberAnimation { from: 0.55; to: 1.0; duration: 600 }
                                        NumberAnimation { from: 1.0; to: 0.55; duration: 600 }
                                    }
                                }
                            }

                            WheelHandler {
                                onWheel: function(event) {
                                    var zoomDelta = event.angleDelta.y / 120.0;
                                    var factor = Math.pow(1.15, zoomDelta);
                                    var newScale = Math.max(1.0, Math.min(50.0, imageContainer.scale * factor));
                                    
                                    var point = event.point.position;
                                    var imageX = flickable.contentX + point.x;
                                    var imageY = flickable.contentY + point.y;
                                    
                                    var oldScale = imageContainer.scale;
                                    imageContainer.scale = newScale;
                                    
                                    var newImageX = imageX * (newScale / oldScale);
                                    var newImageY = imageY * (newScale / oldScale);
                                    
                                    var newContentWidth = imageContainer.width * newScale;
                                    var newContentHeight = imageContainer.height * newScale;
                                    var newLeftMargin = Math.max(0, (flickable.width - newContentWidth) / 2);
                                    var newTopMargin = Math.max(0, (flickable.height - newContentHeight) / 2);
                                    
                                    var minX = -newLeftMargin;
                                    var maxX = newContentWidth > flickable.width ? newContentWidth - flickable.width : minX;
                                    var minY = -newTopMargin;
                                    var maxY = newContentHeight > flickable.height ? newContentHeight - flickable.height : minY;
                                    
                                    flickable.contentX = Math.max(minX, Math.min(newImageX - point.x, maxX));
                                    flickable.contentY = Math.max(minY, Math.min(newImageY - point.y, maxY));
                                }
                            }

                            PinchHandler {
                                target: null
                                onActiveChanged: if (active) {
                                    imageContainer.baseScale = imageContainer.scale;
                                }
                                onScaleChanged: {
                                    var newScale = Math.max(1.0, Math.min(50.0, imageContainer.baseScale * scale));
                                    var point = centroid.position;
                                    
                                    var imageX = flickable.contentX + point.x;
                                    var imageY = flickable.contentY + point.y;
                                    
                                    var oldScale = imageContainer.scale;
                                    imageContainer.scale = newScale;
                                    
                                    var newImageX = imageX * (newScale / oldScale);
                                    var newImageY = imageY * (newScale / oldScale);
                                    
                                    var newContentWidth = imageContainer.width * newScale;
                                    var newContentHeight = imageContainer.height * newScale;
                                    var newLeftMargin = Math.max(0, (flickable.width - newContentWidth) / 2);
                                    var newTopMargin = Math.max(0, (flickable.height - newContentHeight) / 2);
                                    
                                    var minX = -newLeftMargin;
                                    var maxX = newContentWidth > flickable.width ? newContentWidth - flickable.width : minX;
                                    var minY = -newTopMargin;
                                    var maxY = newContentHeight > flickable.height ? newContentHeight - flickable.height : minY;
                                    
                                    flickable.contentX = Math.max(minX, Math.min(newImageX - point.x, maxX));
                                    flickable.contentY = Math.max(minY, Math.min(newImageY - point.y, maxY));
                                }
                            }
                        }

                        Column {
                            visible: !localSR.imageReady
                            anchors.centerIn: parent
                            spacing: 10
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: "＋"
                                color: "#46536a"
                                font.pixelSize: 42
                            }
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: "Your image will appear here"
                                color: "#7d899c"
                                font.pixelSize: 14
                            }
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: "Processing stays on this computer"
                                color: "#4f5c70"
                                font.pixelSize: 11
                            }
                        }

                        DropArea {
                            anchors.fill: parent
                            onDropped: function(drop) {
                                if (drop.hasUrls && drop.urls.length > 0)
                                    localSR.setImageFromUrl(drop.urls[0].toString())
                            }
                        }

                        Rectangle {
                            visible: localSR.imageReady
                            anchors.top: parent.top
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.topMargin: 14
                            width: previewToggle.implicitWidth + 20
                            height: 34
                            radius: 17
                            color: "#c90c1119"
                            border.color: "#344055"
                            RowLayout {
                                id: previewToggle
                                anchors.centerIn: parent
                                spacing: 4
                                Repeater {
                                    model: ["Source", "Live output"]
                                    delegate: Rectangle {
                                        required property string modelData
                                        required property int index
                                        width: label.implicitWidth + 18
                                        height: 26
                                        radius: 13
                                        color: (window.showLiveResult ? index === 1 : index === 0)
                                            ? "#313a61" : "transparent"
                                        Text {
                                            id: label
                                            anchors.centerIn: parent
                                            text: modelData
                                            color: "#dce4f0"
                                            font.pixelSize: 10
                                            font.weight: Font.Medium
                                        }
                                        MouseArea {
                                            anchors.fill: parent
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: window.showLiveResult = index === 1
                                        }
                                    }
                                }
                            }
                        }

                        Rectangle {
                            visible: localSR.imageReady
                            anchors.left: parent.left
                            anchors.bottom: parent.bottom
                            anchors.margins: 14
                            width: outputBadge.implicitWidth + 20
                            height: 32
                            radius: 10
                            color: "#d00d131c"
                            border.color: "#2d394c"
                            Text {
                                id: outputBadge
                                anchors.centerIn: parent
                                text: localSR.predictedSize
                                color: "#b8c3d5"
                                font.pixelSize: 10
                                font.weight: Font.Medium
                            }
                        }
                    }
                }
            }

            ScrollView {
                Layout.preferredWidth: 340
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                contentWidth: availableWidth

                ColumnLayout {
                    width: parent.width
                    spacing: 12

                    Panel {
                        Layout.fillWidth: true
                        ColumnLayout {
                            width: parent.width
                            spacing: 9
                            SectionLabel { text: "Output" }
                            Text { text: "Scale"; color: muted; font.pixelSize: 10 }
                            ModernCombo {
                                Layout.fillWidth: true
                                model: localSR.scaleRows
                                textRole: "label"
                                currentIndex: localSR.scaleIndex
                                enabled: model.length > 0
                                onActivated: function(index) { localSR.setScaleIndex(index) }
                            }
                            Text { text: "Format"; color: muted; font.pixelSize: 10 }
                            ModernCombo {
                                Layout.fillWidth: true
                                model: localSR.formatRows
                                currentIndex: localSR.formatIndex
                                onActivated: function(index) { localSR.setFormatIndex(index) }
                            }
                            Text { text: "Folder"; color: muted; font.pixelSize: 10 }
                            Text {
                                Layout.fillWidth: true
                                text: localSR.outputDirectory
                                color: "#b1bdcf"
                                font.pixelSize: 10
                                elide: Text.ElideMiddle
                            }
                            ModernButton {
                                Layout.fillWidth: true
                                text: "Choose output folder"
                                onClicked: localSR.chooseOutputDirectory()
                            }
                        }
                    }

                    Panel {
                        Layout.fillWidth: true
                        ColumnLayout {
                            width: parent.width
                            spacing: 9
                            RowLayout {
                                Layout.fillWidth: true
                                SectionLabel { text: "Hardware" }
                                Item { Layout.fillWidth: true }
                                Rectangle {
                                    Layout.preferredWidth: 8
                                    Layout.preferredHeight: 8
                                    radius: 4
                                    color: localSR.pressureColor
                                }
                            }
                            ModernCombo {
                                Layout.fillWidth: true
                                model: localSR.deviceRows
                                textRole: "name"
                                currentIndex: localSR.deviceIndex
                                onActivated: function(index) { localSR.setDeviceIndex(index) }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    text: "Memory pressure"
                                    color: muted
                                    font.pixelSize: 10
                                }
                                Item { Layout.fillWidth: true }
                                Text {
                                    text: Math.round(localSR.pressurePercent) + "%"
                                    color: localSR.pressureColor
                                    font.pixelSize: 10
                                    font.weight: Font.DemiBold
                                }
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 7
                                radius: 4
                                color: "#222b39"
                                Rectangle {
                                    width: parent.width * Math.min(1, localSR.pressurePercent / 100)
                                    height: parent.height
                                    radius: 4
                                    color: localSR.pressureColor
                                    Behavior on width { NumberAnimation { duration: 220 } }
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                visible: localSR.mpsMemoryText.length > 0
                                text: localSR.mpsMemoryText
                                color: "#8f9db1"
                                font.pixelSize: 9
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                Layout.fillWidth: true
                                text: localSR.hardwareText
                                color: "#aab6c8"
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    Panel {
                        Layout.fillWidth: true
                        ColumnLayout {
                            width: parent.width
                            spacing: 8
                            SectionLabel { text: "Estimate" }
                            Text {
                                Layout.fillWidth: true
                                text: localSR.estimateText
                                color: "#aeb9ca"
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                Layout.fillWidth: true
                                text: localSR.memoryText
                                color: "#7f8da2"
                                font.pixelSize: 9
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                visible: localSR.warningText.length > 0
                                Layout.fillWidth: true
                                text: localSR.warningText
                                color: "#f2b451"
                                font.pixelSize: 10
                                font.weight: Font.Medium
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: advancedColumn.implicitHeight + 28
                        radius: 16
                        color: "#121822"
                        border.color: advancedOpen ? "#39375f" : "#222c3b"
                        ColumnLayout {
                            id: advancedColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 14
                            spacing: 9
                            Item {
                                Layout.fillWidth: true
                                implicitHeight: 26
                                RowLayout {
                                    anchors.fill: parent
                                    Text {
                                        text: "Advanced settings"
                                        color: "#e3e9f3"
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                    }
                                    Item { Layout.fillWidth: true }
                                    Text {
                                        text: advancedOpen ? "−" : "+"
                                        color: "#9ca8ba"
                                        font.pixelSize: 19
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: advancedOpen = !advancedOpen
                                }
                            }

                            ColumnLayout {
                                visible: advancedOpen
                                Layout.fillWidth: true
                                spacing: 8
                                Text { text: "Tile size"; color: muted; font.pixelSize: 10 }
                                ModernCombo {
                                    Layout.fillWidth: true
                                    model: localSR.tileRows
                                    currentIndex: localSR.tileIndex
                                    onActivated: function(index) { localSR.setTileIndex(index) }
                                }
                                Text { text: "Halo / overlap"; color: muted; font.pixelSize: 10 }
                                ModernCombo {
                                    Layout.fillWidth: true
                                    model: localSR.haloRows
                                    currentIndex: localSR.haloIndex
                                    onActivated: function(index) { localSR.setHaloIndex(index) }
                                }
                                Text { text: "Precision"; color: muted; font.pixelSize: 10 }
                                ModernCombo {
                                    Layout.fillWidth: true
                                    model: localSR.precisionRows
                                    currentIndex: localSR.precisionIndex
                                    onActivated: function(index) { localSR.setPrecisionIndex(index) }
                                }
                                CheckBox {
                                    text: "Safe Memory Mode"
                                    checked: localSR.safeMemory
                                    enabled: localSR.safeMemoryEditable
                                    onToggled: localSR.setSafeMemory(checked)
                                    palette.text: "#cbd4e2"
                                }
                                CheckBox {
                                    text: "Preserve metadata"
                                    checked: localSR.preserveMetadata
                                    onToggled: localSR.setPreserveMetadata(checked)
                                    palette.text: "#cbd4e2"
                                }
                                Text {
                                    text: "JPEG quality  " + localSR.jpegQuality
                                    color: muted
                                    font.pixelSize: 10
                                }
                                Slider {
                                    Layout.fillWidth: true
                                    from: 70
                                    to: 100
                                    stepSize: 1
                                    value: localSR.jpegQuality
                                    onMoved: localSR.setJpegQuality(Math.round(value))
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        
                        Rectangle {
                            Layout.preferredWidth: pressureRow.implicitWidth + 22
                            Layout.preferredHeight: 34
                            radius: 17
                            color: "#141c27"
                            border.color: "#263247"
                            RowLayout {
                                id: pressureRow
                                anchors.centerIn: parent
                                spacing: 7
                                Rectangle {
                                    Layout.preferredWidth: 8
                                    Layout.preferredHeight: 8
                                    radius: 4
                                    color: localSR.pressureColor
                                }
                                Text {
                                    text: localSR.pressureLabel
                                    color: "#b9c4d5"
                                    font.pixelSize: 11
                                    font.weight: Font.Medium
                                }
                            }
                        }

                        ModernButton {
                            Layout.fillWidth: true
                            text: "Refresh hardware"
                            implicitHeight: 34
                            onClicked: localSR.refreshHardware()
                        }
                    }
                    Item { Layout.preferredHeight: 8 }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 88
            color: "#0d121a"
            border.color: "#202938"

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 22
                anchors.rightMargin: 22
                spacing: 14

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    opacity: (localSR.progress > 0 || localSR.canCancel || localSR.progressText !== "") ? 1.0 : 0.0
                    Behavior on opacity { NumberAnimation { duration: 200 } }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: localSR.progressText
                            color: "#d6deeb"
                            font.pixelSize: 12
                            font.weight: Font.Medium
                            elide: Text.ElideRight
                        }
                        Text {
                            text: Math.round(localSR.progress * 100) + "%"
                            color: localSR.progress > 0 ? teal : "#69768a"
                            font.pixelSize: 11
                            font.weight: Font.Bold
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 7
                        radius: 4
                        color: "#222b39"
                        Rectangle {
                            width: parent.width * localSR.progress
                            height: parent.height
                            radius: 4
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0; color: "#7064ef" }
                                GradientStop { position: 1; color: teal }
                            }
                            Behavior on width { NumberAnimation { duration: 180 } }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: localSR.liveResourceText
                        color: "#758298"
                        font.pixelSize: 9
                        elide: Text.ElideRight
                    }
                }

                ModernButton {
                    text: "Cancel"
                    variant: "danger"
                    visible: localSR.canCancel
                    enabled: localSR.canCancel
                    onClicked: localSR.cancelUpscale()
                }
                ModernButton {
                    text: "Reveal"
                    visible: localSR.canOpenResult
                    enabled: localSR.canOpenResult
                    onClicked: localSR.revealResult()
                }
                ModernButton {
                    text: "Open result"
                    visible: localSR.canOpenResult
                    enabled: localSR.canOpenResult
                    onClicked: localSR.openResult()
                }
                ModernButton {
                    implicitWidth: 164
                    implicitHeight: 48
                    text: "Upscale image"
                    variant: "primary"
                    enabled: localSR.canStart
                    onClicked: {
                        window.showLiveResult = true
                        localSR.startUpscale()
                    }
                }
            }
        }
    }
}
