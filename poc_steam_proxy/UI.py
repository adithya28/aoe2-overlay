"""
Age of Empires II – Match Viewer (PyQt6)
Frameless Twitch overlay with web‑based display.
"""
import json
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtGui import QFont, QFontDatabase, QPalette
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QFormLayout, QLineEdit, QPushButton, QFileDialog, QMessageBox, QHBoxLayout, QDialog, QLabel
)

# ----- Config & helpers (unchanged) -----
CONFIG_FILE = Path(__file__).parent / "ui_config.json"


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_config(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=2)


FONT_FILE = Path(__file__).parent / "my_font.ttf"


def load_custom_font(app: QApplication):
    if FONT_FILE.exists():
        font_id = QFontDatabase.addApplicationFont(str(FONT_FILE))
        if font_id != -1:
            family = QFontDatabase.applicationFontFamilies(font_id)[0]
            app.setFont(QFont(family, 9))
        else:
            print("Failed to load custom font")

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QFormLayout(self)

        self.edit_exe = QLineEdit()
        self.edit_exe.setPlaceholderText("C:\\Program Files\\Steam\\steamapps\\common\\AoE2DE\\AoE2DE_s.exe")
        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self.browse_exe)
        hbox_exe = QHBoxLayout()
        hbox_exe.addWidget(self.edit_exe)
        hbox_exe.addWidget(btn_browse)
        layout.addRow("AoE2 EXE Path:", hbox_exe)

        self.edit_profile = QLineEdit()
        self.edit_profile.setPlaceholderText("Your Steam profile ID (e.g., 24148068)")
        layout.addRow("Steam Profile ID:", self.edit_profile)

        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self.save_settings)
        layout.addRow(btn_save)

        self.load_values()

    def load_values(self):
        config = load_config()
        self.edit_exe.setText(config.get("aoe2_exe_path", ""))
        self.edit_profile.setText(config.get("steam_profile_id", ""))

    def browse_exe(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select AoE2 Executable", "", "Executables (*.exe)")
        if file_path:
            self.edit_exe.setText(file_path)

    def save_settings(self):
        config = load_config()
        config["aoe2_exe_path"] = self.edit_exe.text()
        config["steam_profile_id"] = self.edit_profile.text()
        save_config(config)
        QMessageBox.information(self, "Settings", "Settings saved.")
        self.accept()

class CustomTitleBar(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self._parent = parent
        self.setFixedHeight(30)
        self.setAutoFillBackground(True)
        self.setStyleSheet("""
            background-color: rgba(30, 30, 30, 200);
            color: white;
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(4)

        # Title text
        self.title = QLabel("AoE2 Match Viewer")
        self.title.setStyleSheet("font-weight: bold; background: transparent;")
        layout.addWidget(self.title)
        layout.addStretch()

        # ---- Gear button (settings) ----
        btn_gear = QPushButton("⚙")
        btn_gear.setFixedSize(24, 24)
        btn_gear.setFont(QFont("", 12))
        btn_gear.setStyleSheet("""
            QPushButton { background: transparent; color: white; border: none; }
            QPushButton:hover { background: #555; }
        """)
        btn_gear.setToolTip("Settings")
        btn_gear.clicked.connect(self._parent.open_settings)
        layout.addWidget(btn_gear)

        # Minimize button
        btn_min = QPushButton("–")
        btn_min.setFixedSize(24, 24)
        btn_min.setStyleSheet("""
            QPushButton { background: transparent; color: white; border: none; }
            QPushButton:hover { background: #555; }
        """)
        btn_min.clicked.connect(self._parent.showMinimized)
        layout.addWidget(btn_min)

        # Maximize / restore button
        self.btn_max = QPushButton("□")
        self.btn_max.setFixedSize(24, 24)
        self.btn_max.setStyleSheet("""
            QPushButton { background: transparent; color: white; border: none; }
            QPushButton:hover { background: #555; }
        """)
        self.btn_max.clicked.connect(self._toggle_maximize)
        layout.addWidget(self.btn_max)

        # Close button
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(24, 24)
        btn_close.setStyleSheet("""
            QPushButton { background: transparent; color: white; border: none; }
            QPushButton:hover { background: #e81123; }
        """)
        btn_close.clicked.connect(self._parent.close)
        layout.addWidget(btn_close)

        self._drag = None

    def _toggle_maximize(self):
        if self._parent.isMaximized():
            self._parent.showNormal()
            self.btn_max.setText("□")
        else:
            self._parent.showMaximized()
            self.btn_max.setText("❐")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self._parent.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._parent.move(event.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = None
        super().mouseReleaseEvent(event)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AoE2 Match Viewer")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Frameless + always on top
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
        )

        # Central widget (transparent)
        central = QWidget()
        central.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        central.setStyleSheet("background: transparent;")
        central.setAutoFillBackground(False)
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Custom title bar
        self.title_bar = CustomTitleBar(self)
        main_layout.addWidget(self.title_bar)

        # Web view
        self.web = QWebEngineView()
        self.web.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.web.setStyleSheet("background: transparent;")
        self.web.setAutoFillBackground(False)
        pal = self.web.palette()
        pal.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.transparent)
        self.web.setPalette(pal)
        self.web.page().setBackgroundColor(Qt.GlobalColor.transparent)

        html_path = Path(__file__).parent / "overlay.html"
        self.web.load(QUrl.fromLocalFile(str(html_path)))
        main_layout.addWidget(self.web)

        # --- Resize via web view ---
        self.web.installEventFilter(self)                # watch mouse on web view
        self.web.setMouseTracking(True)                 # enable mouse tracking
        self._resize_margin = 6                         # pixels from edge to start resize
        self._start_resize_edge = None                  # used in event filter

        # Initial content‑based size
        self.web.loadFinished.connect(self._adjust_initial_size)

    def _adjust_initial_size(self):
        js = "[document.body.scrollWidth, document.body.scrollHeight];"
        self.web.page().runJavaScript(js, self._on_content_size)
        try:
            self.web.loadFinished.disconnect(self._adjust_initial_size)
        except TypeError:
            pass

    def _on_content_size(self, size):
        if size and len(size) == 2 and size[0] and size[1]:
            w = max(300, int(size[0]) + 4)
            h = max(120, int(size[1]) + 34)   # 34 = title bar height
            self.resize(w, h)

    # ---------- Edge resizing via event filter ----------
    def eventFilter(self, obj, event):
        if obj == self.web:
            if event.type() == event.Type.MouseMove:
                self._update_cursor_and_resize(event)
            elif event.type() == event.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._start_system_resize(event)
            elif event.type() == event.Type.MouseButtonRelease:
                self._start_resize_edge = None
        return super().eventFilter(obj, event)

    def _update_cursor_and_resize(self, event):
        pos = event.position()
        x, y = pos.x(), pos.y()
        w, h = self.web.width(), self.web.height()
        m = self._resize_margin

        # Determine edge (skip top edge because title bar is there)
        left = x <= m
        right = x >= w - m
        bottom = y >= h - m

        if bottom and right:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif bottom and left:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif left:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif right:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif bottom:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _start_system_resize(self, event):
        pos = event.position()
        x, y = pos.x(), pos.y()
        w, h = self.web.width(), self.web.height()
        m = self._resize_margin
        edge = None

        if x <= m:
            edge = Qt.Edge.LeftEdge
        elif x >= w - m:
            edge = Qt.Edge.RightEdge
        if y >= h - m:
            edge = Qt.Edge.BottomEdge if edge is None else edge | Qt.Edge.BottomEdge

        if edge is not None and self.windowHandle():
            self.windowHandle().startSystemResize(edge)
            self._start_resize_edge = edge

    def open_settings(self):
        dialog = SettingsDialog(self)
        dialog.exec()

    # We don't need mousePressEvent override – it’s all in the filter