from __future__ import annotations

def set_ax_label(ctrl, label: str) -> bool:
    if not label:
        return False
    try:
        import objc
    except ImportError:
        return False
    try:
        handle = ctrl.GetHandle()
    except Exception:
        return False
    if not handle:
        return False
    try:
        view = objc.objc_object(c_void_p=handle)
    except Exception:
        return False
    if view is None:
        return False
    done = _apply(view, label)
    try:
        inner = view.documentView() if hasattr(view, 'documentView') else None
    except Exception:
        inner = None
    if inner is not None:
        done = _apply(inner, label) or done
    return done

def _apply(view, label: str) -> bool:
    if not hasattr(view, 'setAccessibilityLabel_'):
        return False
    try:
        view.setAccessibilityLabel_(label)
        return True
    except Exception:
        return False

def label_control(ctrl, label: str) -> None:
    try:
        ctrl.SetName(label)
    except Exception:
        pass
    if set_ax_label(ctrl, label):
        return
    if hasattr(ctrl, 'SetHint'):
        try:
            ctrl.SetHint(label)
        except Exception:
            pass
    try:
        ctrl.SetToolTip(label)
    except Exception:
        pass
