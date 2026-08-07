import QtQuick
import QtQuick.Controls

Button {
    id: control
    property string variant: "secondary"
    property color accent: "#7c6cff"

    implicitHeight: 42
    leftPadding: 16
    rightPadding: 16
    font.pixelSize: 13
    font.weight: Font.DemiBold
    hoverEnabled: true

    contentItem: Text {
        text: control.text
        color: !control.enabled ? "#697487"
            : control.variant === "primary" ? "white"
            : control.variant === "danger" ? "#ff9aaa"
            : "#dce5f4"
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: 11
        color: !control.enabled ? "#151b25"
            : control.variant === "primary" ? (control.down ? "#6152dc" : control.accent)
            : control.variant === "danger" ? (control.down ? "#3a1e28" : "#281923")
            : control.down ? "#273142"
            : control.hovered ? "#222c3b" : "#1a2230"
        border.color: control.variant === "primary" ? "#958aff"
            : control.variant === "danger" ? "#743445" : "#303c50"
        opacity: control.enabled ? 1.0 : 0.75
    }
}
