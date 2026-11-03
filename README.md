# Logicサンプルローダー（Logic Sample Loader）

Logic Pro で、Splice などのサンプルを聴きながら選んで、Quick Sampler、Drum Machine Designer のパッド、
オーディオトラック（プロジェクトのテンポとキーに合わせて）に入れる macOS のアプリです。
VoiceOver とキーボードだけで使えるように作っています。

- 動く環境: Apple シリコン（M1 以降）の Mac、macOS 15 以降、Logic Pro 12.3（英語か日本語の表示）、VoiceOver
- 使うだけなら: [Releases](https://github.com/Takahashi618/logic-sample-loader/releases) にある `LogicSampleLoader_1.2.1.zip`（表示名は Logicサンプルローダー_1.2.1.zip）を開き、中の「はじめにお読みください.txt」を読んでください
- 使い方: [docs/manual.txt](docs/manual.txt)（HTML 版は [docs/manual.html](docs/manual.html)。ダウンロードしてブラウザで開きます）
- ライセンス: MIT ライセンス（Copyright (c) 2026 Takahashi）。全文は [LICENSE.txt](LICENSE.txt)。
  配布版のアプリに入っているほかの人の部品の条件は [同梱部品のライセンス.txt](同梱部品のライセンス.txt)
- このソフトウェアは、作者（Takahashi）の指示と判断のもとで、AI（Anthropic 社の Claude）を使って作りました。
  プログラムのコードと文章の大部分は AI が書いたものです

## ソースから動かす

Python 3.14 と、[requirements.txt](requirements.txt) の部品を入れます。オーディオトラックに貼るには ffmpeg も要ります
（`brew install ffmpeg`。`brew install rubberband` もあれば音質が上がります）。

```bash
python3.14 -m pip install -r requirements.txt
python3.14 app.py
```

- Logic を操作するので、動かすアプリ（端末など）にアクセシビリティの許可が要ります
- テスト: `python3.14 -X utf8 -m unittest discover -s tests -t .`（Logic には触りません）
- 配布版（zip）を作る: `python3.14 -X utf8 tools/build_release.py`（PyInstaller を使います）
- Keyboard Maestro の補助を入れる: `python3.14 -X utf8 tools/install_qs_helper.py --import`

### 中身

- `app.py`・`core/`・`ui/`・`integration/`: アプリ本体（wxPython）
- `tools/qs_sample_menu.py`: Logic の部品を AX で操作する補助。Logic の部品の名前（英語・日本語）は、この中の 1 か所にまとめてあります
- `siblings/`: 作者の別のプロジェクトから借りている部品の写し（読み上げと試聴、種別、素材の索引）。コメントを除いた形で入れています
- `docs/`: マニュアルと「はじめにお読みください」の元
- コメントには、開発中に実機で測って分かったことを日付つきで残しています（Logic Pro 12.3 で確かめた値です）

## English

An app for Logic Pro on macOS: listen to samples (from Splice or elsewhere), choose one, and load it into
Quick Sampler, a Drum Machine Designer pad, or an audio track (matched to the project tempo and key).
It is designed to be used with VoiceOver and the keyboard only.

- Requirements: a Mac with Apple silicon (M1 or later), macOS 15 or later, Logic Pro 12.3 (displayed in English or Japanese), VoiceOver
- To just use it: open `LogicSampleLoader_1.2.1.zip` from [Releases](https://github.com/Takahashi618/logic-sample-loader/releases) and read “README (English).txt” inside
- Manual: [docs/manual_en.txt](docs/manual_en.txt) (HTML version: [docs/manual_en.html](docs/manual_en.html))
- License: MIT License (Copyright (c) 2026 Takahashi). Full text in [LICENSE.txt](LICENSE.txt).
  Third-party components in the packaged app: [同梱部品のライセンス.txt](同梱部品のライセンス.txt)
- This software was made by the author (Takahashi) using AI (Claude by Anthropic), under the author's direction and judgment.
  Most of the code and text was written by the AI

### Running from source

Install Python 3.14 and [requirements.txt](requirements.txt), then run `python3.14 app.py`. Pasting on audio tracks needs ffmpeg
(`brew install ffmpeg`; `brew install rubberband` improves the sound). The app you run it from (such as Terminal) needs
Accessibility permission, because it operates Logic. Code comments are mostly in Japanese.
Tests: `python3.14 -X utf8 -m unittest discover -s tests -t .` Packaging: `python3.14 -X utf8 tools/build_release.py`
