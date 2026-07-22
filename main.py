"""
agentTumx - GUI Terminal Workspace (PyQt6)
"""
import sys, os, json, re
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QSplitter, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QStatusBar, QSystemTrayIcon,
    QWidget, QVBoxLayout, QInputDialog, QMenu
)
from PyQt6.QtCore import Qt, QProcess, QTimer
from PyQt6.QtGui import QFont, QAction, QColor, QTextCursor, QShortcut, QKeySequence

CONFIG_DIR = Path.home() / ".agentTumx"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_CONFIG = {"projects": [], "ssh_hosts": [], "agents": [], "sidebar_visible": True}

def load_config():
    if CONFIG_FILE.exists():
        return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

_ansi_re = re.compile(r'\x1b\[([0-9;]*)m')
_COLORS = ["black","red","green","#ccaa00","blue","magenta","cyan","#d0d0d0",
           "#808080","#ff5555","#55ff55","#ffff55","#5555ff","#ff55ff","#55ffff","white"]

def ansi_to_html(text):
    escaped = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\n","<br>")
    parts = _ansi_re.split(escaped)
    out = []
    st = []
    for i, p in enumerate(parts):
        if i % 2 == 0:
            out.append(f'<span style="{";".join(st)}">{p}</span>' if st else p)
        else:
            st = []
            if p and p != "0":
                for c in p.split(";"):
                    if not c: continue
                    try: c = int(c)
                    except: continue
                    if 30 <= c <= 37: st.append(f"color:{_COLORS[c-30]}")
                    elif 90 <= c <= 97: st.append(f"color:{_COLORS[c-90+8]}")
                    elif c == 1: st.append("font-weight:bold")
    return "".join(out)

_key_map = {
    Qt.Key.Key_Return: "\r", Qt.Key.Key_Backspace: "\x7f", Qt.Key.Key_Tab: "\t",
    Qt.Key.Key_Escape: "\x1b", Qt.Key.Key_Delete: "\x1b[3~",
    Qt.Key.Key_Up: "\x1b[A", Qt.Key.Key_Down: "\x1b[B",
    Qt.Key.Key_Left: "\x1b[D", Qt.Key.Key_Right: "\x1b[C",
    Qt.Key.Key_Home: "\x1b[H", Qt.Key.Key_End: "\x1b[F",
    Qt.Key.Key_PageUp: "\x1b[5~", Qt.Key.Key_PageDown: "\x1b[6~",
}


class TerminalEdit(QTextEdit):
    """QTextEdit that forwards all keystrokes to a shell process."""

    def __init__(self, process: QProcess):
        super().__init__()
        self.proc = process
        self.setReadOnly(False)
        self.setFont(QFont("Cascadia Code, Consolas, Courier New", 10))
        self.setStyleSheet("background:#1e1e1e; color:#d4d4d4; border:none;")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setAcceptRichText(False)
        self._buffer = ""


    def keyPressEvent(self, event):
        key = event.key()
        mod = event.modifiers()
        ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)

        # App shortcuts (handled by global shortcuts, don't forward)
        if ctrl and key in (Qt.Key.Key_N, Qt.Key.Key_W, Qt.Key.Key_B, Qt.Key.Key_Q, Qt.Key.Key_Tab):
            super().keyPressEvent(event)
            return

        # Ctrl+Shift+C/V for copy/paste
        if ctrl and key == Qt.Key.Key_C:
            if self.textCursor().hasSelection():
                super().keyPressEvent(event)
                return
            self._write("\x03"); return
        if ctrl and key == Qt.Key.Key_V:
            txt = QApplication.clipboard().text()
            if txt: self._write(txt)
            return

        # Navigation / special keys
        if key in _key_map:
            self._write(_key_map[key]); return

        # Ctrl+letter
        if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            self._write(chr(key & 0x1f)); return

        # Printable characters
        text = event.text()
        if text:
            self._write(text)
            return

        super().keyPressEvent(event)

    def _write(self, text):
        if self.proc and self.proc.state() == QProcess.ProcessState.Running:
            self.proc.write(text.encode("utf-8"))


class TerminalTab(QWidget):
    """A single terminal tab: process + output display."""

    def __init__(self, shell="cmd.exe", cwd=None):
        super().__init__()
        self.shell = shell
        self.cwd = cwd or str(Path.home())
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_out)
        self.process.finished.connect(lambda: self._append("[exited]\n", "gray"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.output = TerminalEdit(self.process)
        self.output.setReadOnly(False)
        layout.addWidget(self.output)

        self.process.start(shell, [])

    def _read_out(self):
        data = self.process.readAllStandardOutput().data().decode("utf-8", errors="replace")
        self._append(data)

    def _append(self, text, fallback="#d4d4d4"):
        self.output._buffer += text
        if len(self.output._buffer) > 100000:
            self.output._buffer = self.output._buffer[-50000:]
        html = ansi_to_html(self.output._buffer)
        self.output.setHtml(f'<pre style="font-family:Consolas,Courier New;font-size:10pt;color:{fallback};margin:0;">{html}</pre>')
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    def close_process(self):
        if self.process.state() == QProcess.ProcessState.Running:
            self.process.terminate()
            if not self.process.waitForFinished(2000):
                self.process.kill()


class Sidebar(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setMinimumWidth(160)
        self.setMaximumWidth(300)
        self.setStyleSheet("""
            QTreeWidget { background:#252526; color:#ccc; border:none; font-size:11pt; }
            QTreeWidget::item { padding:4px 8px; }
            QTreeWidget::item:hover { background:#333; }
        """)
        self.itemClicked.connect(self._on_click)

    def refresh(self, cfg):
        self.clear()
        def add_group(name):
            g = QTreeWidgetItem(self, [name]); g.setForeground(0, QColor("#4ec9b0"))
            f = g.font(0); f.setBold(True); g.setFont(0, f); return g
        ws = add_group("WORKSPACE")
        for p in cfg.get("projects", []):
            QTreeWidgetItem(ws, [f"  {os.path.basename(p.rstrip('/\\'))}"])
        if cfg.get("ssh_hosts"):
            ssh = add_group("SSH")
            for h in cfg["ssh_hosts"]:
                QTreeWidgetItem(ssh, [f"  {h['name']}"])
        if cfg.get("agents"):
            ag = add_group("AGENTS")
            for a in cfg["agents"]:
                QTreeWidgetItem(ag, [f"  {a['name']}"])
        self.expandAll()

    def _on_click(self, item, col):
        app = self.parent().parent()
        p = item.parent()
        if p and "SSH" in p.text(0):
            name = item.text(0).strip()
            for h in load_config().get("ssh_hosts", []):
                if h["name"] == name:
                    app.open_ssh(h["host"]); break
        elif p and "AGENTS" in p.text(0):
            name = item.text(0).strip()
            for a in load_config().get("agents", []):
                if a["name"] == name:
                    app.open_agent(a); break


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self._init_ui()
        self._init_tray()
        self._init_shortcuts()

    def _init_ui(self):
        self.setWindowTitle("agentTumx")
        self.setGeometry(100, 100, 1100, 750)
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#1e1e1e; }
            QTabWidget::pane { background:#1e1e1e; border:none; }
            QTabBar::tab { background:#2d2d2d; color:#ccc; padding:6px 16px; border:none; min-width:80px; }
            QTabBar::tab:selected { background:#1e1e1e; color:white; border-bottom:2px solid #4ec9b0; }
            QStatusBar { background:#007acc; color:white; font-size:10pt; }
        """)

        menubar = self.menuBar()
        menubar.setStyleSheet("QMenuBar{background:#2d2d2d;color:#ccc;} QMenuBar::item:selected{background:#333;}")
        m = menubar.addMenu("&File")
        def mk_action(text, slot, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut: a.setShortcut(QKeySequence(shortcut))
            return a
        m.addAction(mk_action("New Tab", self.new_tab, "Ctrl+N"))
        m.addAction(mk_action("Close Tab", self.close_tab, "Ctrl+W"))
        m.addAction(mk_action("Toggle Sidebar", self.toggle_sidebar, "Ctrl+B"))
        m.addSeparator()
        m.addAction(mk_action("Quit", self.close, "Ctrl+Q"))
        m2 = menubar.addMenu("&Tools")
        m2.addAction(mk_action("SSH Connect...", self.ssh_dialog))
        m2.addAction(mk_action("Add Project...", self.add_project_dialog))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.sidebar = Sidebar()
        self.sidebar.refresh(self.cfg)
        splitter.addWidget(self.sidebar)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab_idx)
        splitter.addWidget(self.tabs)
        splitter.setSizes([200, 800])
        if not self.cfg.get("sidebar_visible", True):
            self.sidebar.hide()
        self.setCentralWidget(splitter)
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("agentTumx  |  Ctrl+N new tab  Ctrl+W close  Ctrl+B sidebar  Ctrl+Q quit")
        self.new_tab("cmd")

    def _init_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("agentTumx")
        m = QMenu()
        m.addAction("Show/Hide", self.toggle_win)
        m.addSeparator()
        m.addAction("Quit", self.close)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.toggle_win() if r == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def toggle_win(self):
        if self.isVisible(): self.hide()
        else: self.show(); self.raise_(); self.activateWindow()

    def _init_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self.new_tab)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.close_tab)
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self.toggle_sidebar)
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self.close)
        QShortcut(QKeySequence("Ctrl+Tab"), self).activated.connect(self.next_tab)

    def new_tab(self, title="cmd", shell=None, cwd=None):
        t = TerminalTab(shell or "cmd.exe", cwd if cwd else str(Path.home()))
        idx = self.tabs.addTab(t, title)
        self.tabs.setCurrentIndex(idx)
        self.status.showMessage(f"tab: {title}")
        return t

    def close_tab(self):
        self.close_tab_idx(self.tabs.currentIndex())

    def close_tab_idx(self, idx):
        if self.tabs.count() <= 1: return
        w = self.tabs.widget(idx)
        if isinstance(w, TerminalTab): w.close_process()
        self.tabs.removeTab(idx)

    def next_tab(self):
        i = (self.tabs.currentIndex() + 1) % self.tabs.count()
        self.tabs.setCurrentIndex(i)

    def toggle_sidebar(self):
        v = not self.sidebar.isVisible()
        self.sidebar.setVisible(v)
        self.cfg["sidebar_visible"] = v
        save_config(self.cfg)

    def ssh_dialog(self):
        h, ok = QInputDialog.getText(self, "SSH", "host (user@host):")
        if ok and h.strip(): self.open_ssh(h.strip())

    def add_project_dialog(self):
        p, ok = QInputDialog.getText(self, "Add Project", "path:")
        if ok and p.strip():
            if p.strip() not in self.cfg["projects"]:
                self.cfg["projects"].append(p.strip())
                save_config(self.cfg)
                self.sidebar.refresh(self.cfg)

    def open_ssh(self, host):
        self.new_tab(f"ssh:{host}", f"ssh {host}")

    def open_agent(self, agent):
        self.new_tab(agent.get("name","agent"), agent.get("command","cmd.exe"), agent.get("cwd"))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("agentTumx")
    app.setQuitOnLastWindowClosed(False)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
