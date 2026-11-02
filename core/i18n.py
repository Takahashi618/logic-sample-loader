"""表示の言語（日本語・英語）。**Mac の言語の設定に合わせる。** 2026-10-27 から。

海外の利用者向けに、画面・メニュー・読み上げを英語でも出す。言語は起動したときに 1 回だけ決める
（途中で変えるなら、アプリを開き直す）。

### 決め方

1. 環境変数 `LOGICLOADER_LANG` が `ja` か `en` なら、それ（テストと、試しに切り替えるとき用）
2. Mac の「優先する言語」（`NSLocale.preferredLanguages`）を上から見て、**日本語と英語のうち先に出てくるほう**
   （システム設定の「言語と地域」→「アプリケーション」で、このアプリだけ言語を変えたときもこれに入る）
3. どちらも無ければ英語（日本語以外の Mac は英語で出す）

### 書き方

文言は**日本語と英語を並べて書く**（`tr("日本語", "English")`）。f 文字列もそのまま渡せる。
訳を別の表に分けないのは、組み立てる文（「3 小節目の頭」など）が多く、並べたほうが取り違えないため。

**補助（`tools/qs_sample_menu.py`）は 1 つのファイルで Keyboard Maestro から動くので、同じ決め方を
写して持っている。** 片方を変えたら両方を変える（`tests/test_i18n.py` が食い違いに気づく）。
"""

from __future__ import annotations

import os

__all__ = ["LANG", "ENV_NAME", "detect_language", "preferred_languages", "pick_language", "tr", "is_english"]

ENV_NAME = "LOGICLOADER_LANG"
SUPPORTED = ("ja", "en")


def preferred_languages() -> list[str]:
    """Mac の「優先する言語」（例: ["ja-JP", "en-JP"]）。読めなければ環境変数 LANG から 1 つ。"""
    try:
        from Foundation import NSLocale

        langs = [str(x) for x in (NSLocale.preferredLanguages() or [])]
        if langs:
            return langs
    except Exception:
        pass
    env = os.environ.get("LANG", "")
    return [env] if env else []


def pick_language(langs: list[str]) -> str:
    """並びの上から、日本語か英語の先に出てくるほう。どちらも無ければ英語。"""
    for lang in langs:
        head = lang.strip().lower().replace("_", "-").split("-")[0]
        if head in SUPPORTED:
            return head
    return "en"


def detect_language() -> str:
    forced = os.environ.get(ENV_NAME, "").strip().lower()
    if forced in SUPPORTED:
        return forced
    return pick_language(preferred_languages())


LANG = detect_language()


def is_english() -> bool:
    return LANG == "en"


def tr(ja: str, en: str) -> str:
    """いまの言語の文言。"""
    return en if LANG == "en" else ja


# 読み上げの頭に付ける印（音を選ぶ・警告を後ろへ回すときにも見る）
ERROR = tr("[エラー]", "[Error]")
WARNING = tr("[警告]", "[Warning]")
# 文の区切り。日本語は「。」で詰める、英語は「. 」で空ける
PERIOD = tr("。", ".")
SEP = tr("。", ". ")


def sentence(text: str) -> str:
    """文を閉じる（句点が無ければ付ける）。空ならそのまま。"""
    text = text.rstrip()
    if not text:
        return text
    if LANG == "en":
        return text if text.endswith((".", "!", "?")) else text + "."
    return text if text.endswith("。") else text + "。"


def sentences(parts) -> str:
    """文を並べて 1 つの読み上げにする（空の文は飛ばす。最後も閉じる）。"""
    cleaned = [p.rstrip().rstrip("。.").rstrip() for p in parts if p and p.strip()]
    return sentence(SEP.join(cleaned)) if cleaned else ""
