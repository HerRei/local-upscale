import QtQuick
import QtQuick.Layouts

Rectangle {
    id: root
    default property alias content: body.data
    property string title: ""
    property string eyebrow: ""
    property int padding: 16

    radius: 16
    color: "#121822"
    border.color: "#222c3b"
    border.width: 1
    implicitHeight: body.implicitHeight + padding * 2

    ColumnLayout {
        id: body
        anchors.fill: parent
        anchors.margins: root.padding
        spacing: 10
    }
}
