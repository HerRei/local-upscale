import QtQuick
import QtQuick.Layouts

Rectangle {
    id: root
    property string title: ""
    property string subtitle: ""
    property string badge: ""
    property color accent: "#7c6cff"
    signal selected()

    implicitHeight: 96
    radius: 14
    color: mouse.containsMouse ? "#202a39" : "#171f2b"
    border.color: mouse.containsMouse ? accent : "#2a3547"
    scale: mouse.pressed ? 0.985 : 1.0

    Behavior on color { ColorAnimation { duration: 120 } }
    Behavior on border.color { ColorAnimation { duration: 120 } }
    Behavior on scale { NumberAnimation { duration: 90 } }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 12

        Rectangle {
            Layout.preferredWidth: 42
            Layout.preferredHeight: 42
            radius: 12
            color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.18)
            Text {
                anchors.centerIn: parent
                text: root.badge
                color: root.accent
                font.pixelSize: 13
                font.weight: Font.Bold
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            Text {
                text: root.title
                color: "#f4f7fc"
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: root.subtitle
                color: "#8f9bb0"
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                maximumLineCount: 2
                elide: Text.ElideRight
            }
        }

        Text {
            text: "›"
            color: "#76849a"
            font.pixelSize: 25
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.selected()
    }
}
