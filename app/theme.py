from pathlib import Path


_CHECK_ICON_PATH = (
    Path(__file__).resolve().parent / "assets" / "check.svg"
).as_posix().replace('"', '%22')

STYLESHEET = """
QMainWindow, QWidget#root {
    background: #f7f8fa;
    color: #111827;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
}
QDialog#targetDialog {
    background: #ffffff;
    color: #111827;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
}
QLabel#dialogTitle {
    color: #111827;
    font-size: 18px;
    font-weight: 700;
}
QLabel#dialogHint {
    color: #6b7280;
    font-size: 12px;
}
QLabel#dialogNotice {
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 7px;
    color: #374151;
    font-size: 11px;
    padding: 8px 10px;
}
QLabel#logo {
    background: transparent;
    border: none;
}
QLabel#appTitle {
    color: #111827;
    font-size: 23px;
    font-weight: 700;
}
QLabel#appSubtitle, QLabel#fileMeta, QLabel#footerText {
    color: #6b7280;
    font-size: 12px;
}
QLabel#statusChip {
    background: transparent;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    color: #6b7280;
    font-size: 11px;
    font-weight: 500;
    padding: 5px 10px;
}
QLabel#statusChip[state="working"] {
    background: #eff6ff;
    border-color: #dbeafe;
    color: #2563eb;
}
QLabel#statusChip[state="success"] {
    background: #ecfdf5;
    border-color: #d1fae5;
    color: #059669;
}
QFrame#modeSwitch {
    background: #f3f4f6;
    border: none;
    border-radius: 8px;
}
QPushButton#modeButton {
    min-height: 26px;
    max-height: 26px;
    padding: 0 11px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: #6b7280;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#modeButton:hover {
    color: #111827;
}
QPushButton#modeButton:checked {
    background: #ffffff;
    border-color: #e5e7eb;
    color: #111827;
}
QPushButton#modeButton:disabled {
    color: #c4c7ce;
}
QComboBox#languageCombo {
    min-height: 28px;
    max-height: 28px;
    padding: 0 8px;
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 7px;
    color: #374151;
    font-size: 11px;
    font-weight: 500;
}
QComboBox#languageCombo:hover {
    background: #f9fafb;
    border-color: #9ca3af;
}
QComboBox#languageCombo:focus {
    background: #ffffff;
    border-color: #6b7280;
}
QComboBox#languageCombo:on {
    background: #f3f4f6;
    border-color: #9ca3af;
}
QComboBox#languageCombo:disabled {
    background: #f9fafb;
    border-color: #e5e7eb;
    color: #c4c7ce;
}
QComboBox#languageCombo::drop-down {
    width: 20px;
    border: none;
}
QComboBox#languageCombo QAbstractItemView {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    color: #374151;
    outline: none;
    padding: 4px;
    selection-background-color: #f3f4f6;
    selection-color: #111827;
}
QLabel#statusChip[state="error"] {
    background: #fef2f2;
    border-color: #fee2e2;
    color: #dc2626;
}
QCheckBox#recursiveCheckbox {
    color: #6b7280;
    font-size: 11px;
    spacing: 7px;
}
QCheckBox#recursiveCheckbox::indicator {
    width: 16px;
    height: 16px;
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 3px;
}
QCheckBox#recursiveCheckbox::indicator:hover {
    border-color: #9ca3af;
}
QCheckBox#recursiveCheckbox::indicator:checked {
    background: #ffffff;
    border-color: #111827;
    image: url("__CHECK_ICON_PATH__");
}
QCheckBox#recursiveCheckbox::indicator:disabled {
    background: #f9fafb;
    border-color: #e5e7eb;
}
QCheckBox#recursiveCheckbox:disabled {
    color: #c4c7ce;
}
QFrame#dropZone {
    background: #ffffff;
    border: 1px dashed #d1d5db;
    border-radius: 12px;
}
QFrame#dropZone[dragging="true"] {
    background: #fafafa;
    border: 1px solid #111827;
}
QFrame#dropZone:disabled {
    background: #fafafa;
    border-color: #e5e7eb;
}
QLabel#dropIcon {
    color: #9ca3af;
    font-size: 25px;
    font-weight: 400;
}
QLabel#dropTitle {
    color: #111827;
    font-size: 17px;
    font-weight: 600;
}
QLabel#dropDetail {
    color: #9ca3af;
    font-size: 12px;
}
QPushButton {
    min-height: 34px;
    padding: 0 17px;
    border-radius: 7px;
    font-weight: 500;
}
QPushButton#primaryButton {
    background: #111827;
    border: 1px solid #111827;
    color: #ffffff;
}
QPushButton#primaryButton:hover {
    background: #1f2937;
    border-color: #1f2937;
}
QPushButton#primaryButton:pressed {
    background: #030712;
}
QPushButton#secondaryButton {
    background: #ffffff;
    border: 1px solid #d1d5db;
    color: #374151;
}
QPushButton#secondaryButton:hover {
    background: #f9fafb;
    border-color: #9ca3af;
}
QPushButton#secondaryButton:disabled {
    background: #fafafa;
    border-color: #e5e7eb;
    color: #c4c7ce;
}
QPushButton#dangerButton {
    min-height: 26px;
    max-height: 26px;
    padding: 0 9px;
    background: #ffffff;
    border: 1px solid #fecaca;
    border-radius: 6px;
    color: #b91c1c;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#dangerButton:hover {
    background: #fef2f2;
    border-color: #fca5a5;
}
QPushButton#dangerButton:pressed {
    background: #fee2e2;
}
QPushButton#dangerButton[risk="high"] {
    border-color: #fde68a;
    color: #b45309;
}
QPushButton#dangerButton[risk="high"]:hover {
    background: #fffbeb;
    border-color: #fcd34d;
}
QPushButton#dangerButton:disabled {
    background: #f9fafb;
    border-color: #e5e7eb;
    color: #9ca3af;
}
QPushButton#recycleButton {
    min-height: 26px;
    max-height: 26px;
    padding: 0 9px;
    background: #b91c1c;
    border: 1px solid #b91c1c;
    border-radius: 6px;
    color: #ffffff;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#recycleButton:hover {
    background: #991b1b;
    border-color: #991b1b;
}
QPushButton#recycleButton:pressed {
    background: #7f1d1d;
}
QPushButton#recycleButton[risk="high"] {
    background: #b45309;
    border-color: #b45309;
}
QPushButton#recycleButton[risk="high"]:hover {
    background: #92400e;
    border-color: #92400e;
}
QPushButton#recycleButton:disabled {
    background: #f9fafb;
    border-color: #e5e7eb;
    color: #9ca3af;
}
QPushButton#batchButton {
    min-height: 26px;
    max-height: 26px;
    padding: 0 10px;
    background: #ffffff;
    border: 1px solid #fdba74;
    border-radius: 6px;
    color: #9a3412;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#batchButton:hover {
    background: #fff7ed;
    border-color: #fb923c;
}
QPushButton#batchButton:pressed {
    background: #ffedd5;
}
QPushButton#batchButton:disabled {
    background: #f9fafb;
    border-color: #e5e7eb;
    color: #9ca3af;
}
QPushButton#compactButton {
    min-height: 26px;
    max-height: 26px;
    padding: 0 10px;
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    color: #374151;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#compactButton:hover {
    background: #f9fafb;
    border-color: #9ca3af;
}
QPushButton#compactButton:disabled {
    background: #f9fafb;
    border-color: #e5e7eb;
    color: #c4c7ce;
}
QPushButton#moreButton {
    min-height: 26px;
    max-height: 26px;
    padding: 0 8px;
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    color: #374151;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#moreButton:hover {
    background: #f9fafb;
    border-color: #9ca3af;
}
QPushButton#moreButton:pressed {
    background: #f3f4f6;
}
QMenu {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    color: #374151;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 12px;
    padding: 6px;
}
QMenu::item {
    min-width: 164px;
    padding: 8px 30px 8px 12px;
    border-radius: 5px;
}
QMenu::item:selected {
    background: #f3f4f6;
    color: #111827;
}
QMenu::item:disabled {
    color: #c4c7ce;
}
QMenu::separator {
    height: 1px;
    background: #f3f4f6;
    margin: 5px 7px;
}
QFrame#fileCard {
    background: transparent;
    border: none;
    border-radius: 0;
}
QFrame#resultsCard {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
}
QLabel#fileName {
    color: #111827;
    font-size: 14px;
    font-weight: 600;
}
QLabel#sectionTitle {
    color: #111827;
    font-size: 15px;
    font-weight: 600;
}
QLabel#resultHint {
    color: #9ca3af;
    font-size: 12px;
}
QStackedWidget {
    background: transparent;
}
QFrame#resultPlaceholder {
    background: #fafafa;
    border: 1px solid #f3f4f6;
    border-radius: 9px;
}
QLabel#placeholderIcon {
    color: #d1d5db;
    font-size: 30px;
    font-weight: 400;
}
QLabel#placeholderTitle {
    color: #374151;
    font-size: 15px;
    font-weight: 600;
}
QLabel#placeholderDetail {
    color: #9ca3af;
    font-size: 12px;
}
QFrame#resultPlaceholder[tone="working"] QLabel#placeholderIcon {
    color: #2563eb;
}
QFrame#resultPlaceholder[tone="success"] QLabel#placeholderIcon {
    color: #059669;
}
QFrame#resultPlaceholder[tone="error"] QLabel#placeholderIcon {
    color: #dc2626;
}
QProgressBar#scanProgress {
    background: #e5e7eb;
    border: none;
    border-radius: 2px;
    max-height: 3px;
}
QProgressBar#scanProgress::chunk {
    background: #111827;
    border-radius: 2px;
}
QTableWidget#processTable {
    background: #ffffff;
    alternate-background-color: #ffffff;
    border: 1px solid #f3f4f6;
    border-radius: 9px;
    color: #374151;
    outline: none;
    selection-background-color: #f3f4f6;
    selection-color: #111827;
}
QTableWidget#processTable::item {
    border-bottom: 1px solid #f3f4f6;
    padding: 0 8px;
}
QTableWidget#processTable::item:selected {
    background: #f3f4f6;
    color: #111827;
}
QTableWidget#processTable::item:hover {
    background: #fafafa;
}
QTableWidget#targetTable {
    background: #ffffff;
    alternate-background-color: #fafafa;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    color: #374151;
    outline: none;
    selection-background-color: #f3f4f6;
    selection-color: #111827;
}
QTableWidget#targetTable::item {
    border-bottom: 1px solid #f3f4f6;
    padding: 0 8px;
}
QTableWidget#targetTable::item:selected {
    background: #f3f4f6;
    color: #111827;
}
QHeaderView::section {
    background: #ffffff;
    border: none;
    border-bottom: 1px solid #e5e7eb;
    color: #6b7280;
    font-size: 11px;
    font-weight: 600;
    padding: 10px 8px;
}
QTableCornerButton::section {
    background: #ffffff;
    border: none;
}
QLabel#permissionChip {
    background: transparent;
    border: 1px solid #e5e7eb;
    border-radius: 9px;
    color: #9ca3af;
    font-size: 10px;
    padding: 4px 8px;
}
QLabel#permissionChip[state="ok"] {
    border-color: #d1fae5;
    color: #059669;
}
QLabel#permissionChip[state="limited"] {
    border-color: #fde68a;
    color: #b45309;
}
QPushButton#permissionButton {
    min-height: 24px;
    max-height: 24px;
    padding: 0 10px;
    background: #fff7ed;
    border: 1px solid #fed7aa;
    border-radius: 9px;
    color: #b45309;
    font-size: 10px;
    font-weight: 600;
}
QPushButton#permissionButton:hover {
    background: #ffedd5;
    border-color: #fdba74;
    color: #92400e;
}
QPushButton#permissionButton:pressed {
    background: #fed7aa;
}
QPushButton#permissionButton:disabled {
    background: #f9fafb;
    border-color: #e5e7eb;
    color: #c4c7ce;
}
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #d1d5db;
    border-radius: 3px;
    min-height: 28px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QToolTip {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    color: #374151;
    padding: 5px;
}
""".replace("__CHECK_ICON_PATH__", _CHECK_ICON_PATH)
