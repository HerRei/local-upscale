pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    implicitHeight: 42
    leftPadding: 13
    rightPadding: 34
    font.pixelSize: 13
    hoverEnabled: true

    contentItem: Text {
        leftPadding: 2
        text: control.displayText
        color: control.enabled ? "#e6edf8" : "#707b8e"
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Text {
        x: control.width - width - 13
        y: (control.height - height) / 2 - 1
        text: "⌄"
        color: "#94a0b5"
        font.pixelSize: 18
    }

    background: Rectangle {
        radius: 10
        color: control.pressed ? "#222b3a" : "#171f2b"
        border.color: control.activeFocus ? "#7c6cff" : "#2a3547"
    }

    popup: Popup {
        y: control.height + 5
        width: control.width
        implicitHeight: Math.min(contentItem.implicitHeight + 12, 330)
        padding: 6

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }

        background: Rectangle {
            radius: 12
            color: "#171f2b"
            border.color: "#354258"
        }
    }

    delegate: ItemDelegate {
        id: delegateItem
        required property var model
        required property int index
        width: control.width - 12
        height: 38
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            text: control.textRole ? model[control.textRole] : modelData
            color: "#e6edf8"
            font.pixelSize: 13
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 8
            color: delegateItem.highlighted ? "#2a3450" : "transparent"
        }
    }
}
