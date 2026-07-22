"""
agentTumx - GUI Terminal Workspace (PyQt6)
"""
import sys, os, json, re, queue
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

class TerminalTab(QWidget):
    """A single terminal tab: subprocess shell + output display."""

    def __init__(self, shell="cmd.exe", cwd=None):
        super().__init__()
        self.shell = shell
        self.cwd = cwd or str(Path.home())
        self._proc = None
        self._buffer = ""
        self._alive = True
        self._write_queue = queue.Queue()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.output = QTextEdit(self)
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Cascadia Code, Consolas, Courier New", 10))
        self.output.setStyleSheet("background:#1e1e1e; color:#d4d4d4; border:none;")
        self.output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.output.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.output)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Start shell via subprocess
        import subprocess as sp
        self._proc = sp.Popen(
            shell,
            stdin=sp.PIPE,
            stdout=sp.PIPE,
            stderr=sp.STDOUT,
            cwd=self.cwd,
            shell=True,
            bufsize=0,
        )

        # Reader thread
        import threading as th
        self._reader_th = th.Thread(target=self._reader_loop, daemon=True)
        self._reader_th.start()

        # Writer thread
        self._writer_th = th.Thread(target=self._writer_loop, daemon=True)
        self._writer_th.start()

        # Poll timer for display updates
        QTimer.singleShot(200, self.setFocus)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._update_display)
        self._poll_timer.start(50)

    def _reader_loop(self):
        try:
            while self._alive and self._proc and self._proc.stdout:
                data = self._proc.stdout.read(4096)
                if not data:
                    break
                self._buffer += data.decode("utf-8", errors="replace")
                if len(self._buffer) > 100000:
                    self._buffer = self._buffer[-50000:]
        except:
            pass

    def _writer_loop(self):
        try:
            while self._alive and self._proc and self._proc.stdin:
                text = self._write_queue.get()
                if text is None:
                    break
                self._proc.stdin.write(text.encode("utf-8"))
                self._proc.stdin.flush()
        except:
            pass

    def _update_display(self):
        if not self._buffer:
            return
        html = ansi_to_html(self._buffer)
        self.output.setHtml(f'<pre style="font-family:Consolas,Courier New;font-size:10pt;color:#d4d4d4;margin:0;">{html}</pre>')
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    def keyPressEvent(self, event):
        if self._process_key(event):
            event.accept()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)

    def focusInEvent(self, event):
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        super().focusInEvent(event)

    def _process_key(self, event):
        key = event.key()
        mod = event.modifiers()
        ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)

        if ctrl and key in (Qt.Key.Key_N, Qt.Key.Key_W, Qt.Key.Key_B, Qt.Key.Key_Q, Qt.Key.Key_Tab):
            return False

        if ctrl and key == Qt.Key.Key_C:
            if self.output.textCursor().hasSelection():
                self.output.copy()
                return True
            self._send("\x03"); return True

        if ctrl and key == Qt.Key.Key_V:
            txt = QApplication.clipboard().text()
            if txt: self._send(txt)
            return True

        km = {
            Qt.Key.Key_Return: "\r", Qt.Key.Key_Enter: "\r",
            Qt.Key.Key_Backspace: "\x7f", Qt.Key.Key_Tab: "\t",
            Qt.Key.Key_Escape: "\x1b", Qt.Key.Key_Delete: "\x1b[3~",
            Qt.Key.Key_Up: "\x1b[A", Qt.Key.Key_Down: "\x1b[B",
            Qt.Key.Key_Left: "\x1b[D", Qt.Key.Key_Right: "\x1b[C",
            Qt.Key.Key_Home: "\x1b[H", Qt.Key.Key_End: "\x1b[F",
            Qt.Key.Key_PageUp: "\x1b[5~", Qt.Key.Key_PageDown: "\x1b[6~",
        }
        if key in km:
            self._send(km[key]); return True

        if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            self._send(chr(key & 0x1f)); return True

        t = event.text()
        if t:
            self._send(t)
            return True

        return False

    def _send(self, text):
        if self._alive:
            self._write_queue.put(text)

    def close_process(self):
        self._alive = False
        if self._proc:
            self._proc.terminate()
            try: self._proc.wait(timeout=3)
            except: self._proc.kill()


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
        ws = add_group("工作区")
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
        menubar.setStyleSheet("QMenuBar{background:white;color:black;} QMenuBar::item:selected{background:#e0e0e0;} QMenu{background:white;color:black;} QMenu::item:selected{background:#007acc;color:white;}")
        m = menubar.addMenu("&文件")
        def mk_action(text, slot, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut: a.setShortcut(QKeySequence(shortcut))
            return a
        m.addAction(mk_action("新建标签", self.new_tab, "Ctrl+N"))
        m.addAction(mk_action("关闭标签", self.close_tab, "Ctrl+W"))
        m.addAction(mk_action("切换侧边栏", self.toggle_sidebar, "Ctrl+B"))
        m.addSeparator()
        m.addAction(mk_action("退出", self.close, "Ctrl+Q"))
        m2 = menubar.addMenu("&工具")
        m2.addAction(mk_action("SSH 连接...", self.ssh_dialog))
        m2.addAction(mk_action("添加项目...", self.add_project_dialog))

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
        self.status.showMessage("agentTumx  |  Ctrl+N 新建  Ctrl+W 关闭  Ctrl+B 侧边栏  Ctrl+Q 退出")
        self.new_tab("终端")

    def _init_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("agentTumx")
        m = QMenu()
        m.addAction("显示/隐藏", self.toggle_win)
        m.addSeparator()
        m.addAction("退出", self.close)
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
        t.setFocus()
        self.status.showMessage(f"标签: {title}")
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
