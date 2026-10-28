"""Explicit depicted-person review; source mentions are never preselected."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QListWidget,QListWidgetItem,QDialogButtonBox

class RemoteLinksDialog(QDialog):
    def __init__(self,entities,people,groups,parent=None):
        super().__init__(parent);self.setWindowTitle('온라인 사진 · 등장 인물과 그룹 연결');self.resize(520,620)
        layout=QVBoxLayout(self)
        text=QLabel('실제 등장하는 것으로 확인한 인물만 선택하세요.\n제목·별칭 언급이나 제공처 분류는 인물 등장의 증거가 아닙니다.');text.setWordWrap(True);layout.addWidget(text)
        self.people=QListWidget();self.groups=QListWidget()
        for widget,rows,selected in [(self.people,entities['persons'],{p['id'] for p in people}),(self.groups,entities['groups'],{g['id'] for g in groups})]:
            for row in rows:
                item=QListWidgetItem(row['name']);item.setData(Qt.ItemDataRole.UserRole,row['id']);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if row['id'] in selected else Qt.CheckState.Unchecked);widget.addItem(item)
        layout.addWidget(QLabel('확인한 등장 인물'));layout.addWidget(self.people,2)
        layout.addWidget(QLabel('그룹 연결'));layout.addWidget(self.groups,1)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def selected(self,widget):
        return [widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(widget.count()) if widget.item(i).checkState()==Qt.CheckState.Checked]
