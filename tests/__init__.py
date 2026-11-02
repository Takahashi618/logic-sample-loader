import os

# テストは日本語の文言で書いてある。Mac の言語が英語でも日本語で走らせる（英語は tests/test_i18n.py で個別に見る）
os.environ.setdefault("LOGICLOADER_LANG", "ja")
