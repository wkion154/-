TOKENS = dict(background='#15161b', panel='#1c1d23', text='#e8eaf1', muted='#969baa', border='#2b2d36', accent='#b9afff', radius=8, gap=12, font='Segoe UI', button_height=32)
STYLE = '''
QWidget {background:#15161b; color:#e8eaf1; font-family:"Segoe UI", "Noto Sans", sans-serif; font-size:13px;}
QMainWindow, QDialog {background:#15161b;}
QFrame#toolbar {background:#111216;border-bottom:1px solid #292b33;}
QLabel#brand {font-size:14px;font-weight:700;letter-spacing:2px;color:#eeeaff;}
QLabel#eyebrow {color:#b9afff;font-size:11px;font-weight:600;letter-spacing:2px;}
QLabel#heading {font-size:28px;font-weight:600;}
QLabel#muted {color:#969baa;}
QLabel#infoTitle {font-size:15px;font-weight:600;}
QLabel#empty {color:#969baa;font-size:15px;}
QPushButton {background:#24262e;border:1px solid #343640;border-radius:6px;padding:6px 12px;min-height:20px;}
QPushButton:hover {background:#30323e;border-color:#67617e;}
QPushButton:pressed {background:#3a374e;}
QPushButton:disabled {color:#686c79;background:#202127;}
QPushButton#primary {background:#c1b6ff;color:#171320;border:0;font-weight:600;}
QPushButton#primary:hover {background:#d1c8ff;}
QPushButton#nav {text-align:left;background:transparent;border:0;padding:9px 12px;color:#a5a9b7;}
QPushButton#nav:checked {background:#2b293c;color:#e1d9ff;}
QPushButton#nav:hover {background:#24252e;}
QLineEdit,QComboBox,QSpinBox {background:#202128;border:1px solid #343640;border-radius:6px;padding:7px;selection-background-color:#585077;}
QLineEdit:focus {border-color:#a497dd;}
QComboBox QAbstractItemView {background:#23252c;selection-background-color:#39344e;}
QListView {border:0;background:#15161b;outline:none;}
QTreeWidget {background:#191a20;border:0;outline:none;}
QTreeWidget::item {padding:8px 4px;border-radius:4px;}
QTreeWidget::item:selected {background:#302d43;color:#e4dcff;}
QTreeWidget::item:hover {background:#252630;}
QScrollBar:vertical {background:transparent;width:9px;margin:2px;}
QScrollBar::handle:vertical {background:#454752;border-radius:4px;min-height:32px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
QSplitter::handle {background:#292b33;width:1px;}
QStatusBar {background:#111216;color:#969baa;border-top:1px solid #292b33;}
QToolTip {background:#292b35;color:#eee;border:1px solid #454753;padding:6px;}
QGroupBox {border:1px solid #30323b;border-radius:6px;margin-top:12px;padding:12px;}
QGroupBox::title {subcontrol-origin:margin;left:12px;color:#a9a3c1;}
QCheckBox {spacing:8px;}
'''

STYLE += """
QTabWidget::pane {border:1px solid #2b2d36;border-radius:6px;}
QTabBar::tab {background:#191a20;color:#969baa;padding:10px 18px;border-bottom:2px solid transparent;}
QTabBar::tab:selected {color:#ddd5ff;border-bottom:2px solid #b9afff;background:#24222f;}
QTableView {background:#191a20;alternate-background-color:#1e1f26;border:0;gridline-color:#2b2d36;selection-background-color:#322e45;selection-color:#ece6ff;outline:none;}
QHeaderView::section {background:#202128;color:#aeb2c0;border:0;border-bottom:1px solid #343640;padding:9px;}
QTableCornerButton::section {background:#202128;border:0;}
"""
