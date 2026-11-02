from __future__ import annotations
import wx

def press_button(button: wx.Button) -> None:
    click = wx.CommandEvent(wx.EVT_BUTTON.typeId, button.GetId())
    click.SetEventObject(button)
    button.GetEventHandler().ProcessEvent(click)

def handle_dialog_enter_escape(dialog: wx.Dialog, evt: wx.KeyEvent) -> bool:
    if evt.GetModifiers() != wx.MOD_NONE:
        return False
    key = evt.GetKeyCode()
    if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
        focused = wx.Window.FindFocus()
        if isinstance(focused, wx.Button):
            press_button(focused)
            return True
        ok = dialog.FindWindow(dialog.GetAffirmativeId())
        if isinstance(ok, wx.Button):
            press_button(ok)
            return True
        return False
    if key == wx.WXK_ESCAPE:
        cancel = dialog.FindWindow(dialog.GetEscapeId())
        if isinstance(cancel, wx.Button):
            press_button(cancel)
            return True
        return False
    return False
