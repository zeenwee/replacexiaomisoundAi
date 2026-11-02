"""macOS 菜单栏常驻（NSStatusItem）。

右上角一个小图标，点开是原生菜单：
    打开控制台 / 引擎启动 / 引擎停止 / 快速播报… / 退出

「快速播报…」弹一个原生小面板，可以直接打字让音箱念，
不用打开主窗口。

用 pyobjc 直接写 Cocoa，不额外引依赖。
"""

from __future__ import annotations

import threading

from objc import selector


def _appkit():
    from AppKit import (
        NSAlert,
        NSApplication,
        NSBezelStyleRounded,
        NSBackingStoreBuffered,
        NSButton,
        NSImage,
        NSMenu,
        NSMenuItem,
        NSPanel,
        NSStatusBar,
        NSTextField,
        NSVariableStatusItemLength,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskTitled,
        NSWindowStyleMaskUtilityWindow,
    )

    return dict(
        NSAlert=NSAlert,
        NSApplication=NSApplication,
        NSBezelStyleRounded=NSBezelStyleRounded,
        NSBackingStoreBuffered=NSBackingStoreBuffered,
        NSButton=NSButton,
        NSImage=NSImage,
        NSMenu=NSMenu,
        NSMenuItem=NSMenuItem,
        NSPanel=NSPanel,
        NSStatusBar=NSStatusBar,
        NSTextField=NSTextField,
        NSVariableStatusItemLength=NSVariableStatusItemLength,
        NSWindowStyleMaskClosable=NSWindowStyleMaskClosable,
        NSWindowStyleMaskTitled=NSWindowStyleMaskTitled,
        NSWindowStyleMaskUtilityWindow=NSWindowStyleMaskUtilityWindow,
    )


class Menubar:
    def __init__(self, window):
        self.window = window
        self.status_item = None
        self._panel = None
        self._field = None
        self._buttons = None

    # ---------------- 安装 ----------------

    def install(self) -> None:
        A = _appkit()
        bar = A["NSStatusBar"].systemStatusBar()
        self.status_item = bar.statusItemWithLength_(A["NSVariableStatusItemLength"])
        if self.status_item is None:
            raise RuntimeError("拿不到 NSStatusItem")

        button = self.status_item.button()
        image = A["NSImage"].imageWithSystemSymbolName_accessibilityDescription_(
            "waveform.circle.fill", "小智控制台"
        )
        if image is not None:
            button.setImage_(image)
        button.setToolTip_("小智控制台")

        menu = A["NSMenu"].alloc().init()
        menu.addItem_(
            self._item(A, "打开控制台", "openConsole:", "")
        )
        menu.addItem_(A["NSMenuItem"].separatorItem())
        menu.addItem_(self._item(A, "启动引擎", "startEngine:", ""))
        menu.addItem_(self._item(A, "停止引擎", "stopEngine:", ""))
        menu.addItem_(A["NSMenuItem"].separatorItem())
        menu.addItem_(self._item(A, "快速播报…", "quickSpeak:", ""))
        menu.addItem_(A["NSMenuItem"].separatorItem())
        menu.addItem_(self._item(A, "退出", "quitApp:", "q"))
        self.status_item.setMenu_(menu)

    def _item(self, A, title: str, action: str, key: str):
        item = A["NSMenuItem"].alloc().initWithTitle_action_keyEquivalent_(
            title, action, key
        )
        item.setTarget_(self)
        return item

    # ---------------- 动作 ----------------

    def openConsole_(self, sender) -> None:  # noqa: N802 - Cocoa selector
        try:
            self.window.show()
            self.window.restore()
        except Exception:
            pass

    def startEngine_(self, sender) -> None:  # noqa: N802
        from console.backend.engine import ENGINE

        ENGINE.start()

    def stopEngine_(self, sender) -> None:  # noqa: N802
        from console.backend.engine import ENGINE

        ENGINE.stop()

    def quitApp_(self, sender) -> None:  # noqa: N802
        from console.backend.engine import ENGINE

        ENGINE.stop()
        from AppKit import NSApplication

        NSApplication.sharedApplication().terminate_(None)

    # ---------------- 快速播报面板 ----------------

    def quickSpeak_(self, sender) -> None:  # noqa: N802
        A = _appkit()
        if self._panel is None:
            self._build_panel(A)
        self._field.setStringValue_("")
        self._panel.center()
        self._panel.makeKeyAndOrderFront_(None)

    def _build_panel(self, A) -> None:
        style = (
            A["NSWindowStyleMaskTitled"]
            | A["NSWindowStyleMaskClosable"]
            | A["NSWindowStyleMaskUtilityWindow"]
        )
        panel = A["NSPanel"].alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (380, 132)), style, A["NSBackingStoreBuffered"], False
        )
        panel.setTitle_("快速播报")
        panel.setFloatingPanel_(True)

        view = panel.contentView()

        field = A["NSTextField"].alloc().initWithFrame_(((16, 62), (348, 26)))
        field.setPlaceholderString_("要音箱说什么？")
        view.addSubview_(field)

        btn = A["NSButton"].alloc().initWithFrame_(((286, 22), (78, 30)))
        btn.setTitle_("说")
        btn.setBezelStyle_(A["NSBezelStyleRounded"])
        btn.setKeyEquivalent_("\r")
        btn.setTarget_(self)
        btn.setAction_(selector(self.speakFromPanel_))
        view.addSubview_(btn)

        self._panel = panel
        self._field = field
        self._buttons = btn

    def speakFromPanel_(self, sender) -> None:  # noqa: N802
        text = (self._field.stringValue() or "").strip()
        if not text:
            return
        self._panel.orderOut_(None)

        def worker():
            from console.backend.loop import run
            from console.backend.xiaomi import LINK

            try:
                run(LINK.speak(text))
            except Exception as e:
                print(f"快速播报失败：{e}")

        threading.Thread(target=worker, daemon=True).start()
