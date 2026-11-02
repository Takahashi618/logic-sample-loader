from __future__ import annotations
import os
import subprocess
import sys
import threading
_ENV_NO_ANNOUNCE = 'SM_NO_ANNOUNCE'

def _escape_applescript(text: str) -> str:
    text = text.replace('\\', '\\\\')
    text = text.replace('"', '\\"')
    return text

def _announce_native(message: str) -> bool:
    if threading.current_thread() is not threading.main_thread():
        return False
    try:
        from AppKit import NSAccessibilityAnnouncementKey, NSAccessibilityAnnouncementRequestedNotification, NSAccessibilityPostNotificationWithUserInfo, NSAccessibilityPriorityHigh, NSAccessibilityPriorityKey, NSApplication
    except Exception:
        return False
    try:
        app = NSApplication.sharedApplication()
        target = app.keyWindow() or app.mainWindow() or app
        NSAccessibilityPostNotificationWithUserInfo(target, NSAccessibilityAnnouncementRequestedNotification, {NSAccessibilityAnnouncementKey: message, NSAccessibilityPriorityKey: NSAccessibilityPriorityHigh})
        return True
    except Exception:
        return False

def _announce_macos(message: str) -> None:
    safe = _escape_applescript(message)
    script = f'tell application "System Events"\n    set ann to "{safe}"\n    tell (first application process whose frontmost is true)\n        set value of attribute "AXAnnouncement" to ann\n    end tell\nend tell'
    subprocess.Popen(['osascript', '-e', script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
_ao2_speaker = None
_ao2_tried = False

def _announce_windows(message: str) -> None:
    global _ao2_speaker, _ao2_tried
    if not _ao2_tried:
        _ao2_tried = True
        try:
            from accessible_output2.outputs.auto import Auto
            _ao2_speaker = Auto()
        except Exception:
            _ao2_speaker = None
    if _ao2_speaker is not None:
        try:
            _ao2_speaker.speak(message, interrupt=True)
        except Exception:
            pass

def announce(message: str) -> None:
    if not message:
        return
    if os.environ.get(_ENV_NO_ANNOUNCE, '') not in ('', '0'):
        return
    try:
        if sys.platform == 'darwin':
            if not _announce_native(message):
                _announce_macos(message)
        elif sys.platform.startswith('win'):
            _announce_windows(message)
    except Exception:
        pass
