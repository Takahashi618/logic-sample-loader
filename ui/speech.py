"""VoiceOver に読ませる（macOS）。**アプリ自身から「読み上げてほしい」という通知を出す。**

### 2026-09-26 に分かったこと

それまで使っていた「System Events で、前にあるアプリの AXAnnouncement を書く」方法
（借りている `announce.py`）は、この Mac では **-10006（設定できません）で断られ、
何も読まれていなかった。** osascript のエラーは捨てていたので、気づけなかった。
聞こえていたのは、フォーカスのある欄やボタンの値が変わったときに VoiceOver が自分で読んだ分だけ。
だから、一覧にいるままコマンド+右ブラケットでトラックを移ると、トラックの名前が聞こえなかった
（「貼り付け先のトラック」の欄にいるときだけ、欄の値として聞こえる）。

### どう読ませるか

**NSAccessibilityPostNotificationWithUserInfo で AnnouncementRequested を送る**
（VoiceOver の正規の方法。say のような二重読みにならず、VoiceOver の声で読む）。
優先度は高（読み上げ中でも割り込む）。どれも利用者が頼んだ用事の結果なので、聞き逃すと困る。
送れないとき（窓が無い、pyobjc が無い、macOS でない）だけ、前の方法（借りたもの）を試す。

**メインスレッドから呼ぶ**（wx の CallAfter・CallLater の中）。`LOGICLOADER_SILENT=1` なら何もしない。
"""

from __future__ import annotations

import sys

from ui import borrowed

__all__ = ["announce", "NATIVE", "FALLBACK", "SILENT", "NOTHING"]

NATIVE, FALLBACK, SILENT, NOTHING = "native", "fallback", "silent", "nothing"


def _post_native(message: str, high: bool) -> bool:
    """AnnouncementRequested の通知を送る。送れたら True。"""
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import (
            NSAccessibilityAnnouncementKey,
            NSAccessibilityAnnouncementRequestedNotification,
            NSAccessibilityPostNotificationWithUserInfo,
            NSAccessibilityPriorityHigh,
            NSAccessibilityPriorityKey,
            NSAccessibilityPriorityMedium,
            NSApplication,
        )
    except Exception:
        return False
    try:
        app = NSApplication.sharedApplication()
        # 窓に向けて送る（実績のある形）。窓が無ければアプリそのものに
        target = app.keyWindow() or app.mainWindow() or app
        info = {
            NSAccessibilityAnnouncementKey: message,
            NSAccessibilityPriorityKey: NSAccessibilityPriorityHigh if high else NSAccessibilityPriorityMedium,
        }
        NSAccessibilityPostNotificationWithUserInfo(target, NSAccessibilityAnnouncementRequestedNotification, info)
        return True
    except Exception:
        return False


def announce(message: str, *, high: bool = True) -> str:
    """読ませる。どの方法で送ったかを返す（NATIVE / FALLBACK / SILENT / NOTHING。ログに残す）。"""
    if not message:
        return NOTHING
    if borrowed.is_silent():
        return SILENT
    if _post_native(message, high):
        return NATIVE
    borrowed.announce(message)
    return FALLBACK
