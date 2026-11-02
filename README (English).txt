Logic Sample Loader 1.2.0
README

This folder contains 8 files and 1 app. Some file names are in Japanese; the English name follows each.

  Logicサンプルローダー.app  The app (Finder shows it as Logic Sample Loader when your Mac is in English)
  README (English).txt  This document
  Manual (English).html  How to use the app (opens in a browser; you can move by headings)
  Manual (English).txt  The same manual as plain text
  はじめにお読みください.txt  This document in Japanese
  マニュアル.html, マニュアル.txt  The manual in Japanese
  ライセンス.txt  License: the terms of use of this software (MIT License, in English with a Japanese summary)
  同梱部品のライセンス.txt  Third-party licenses: the terms of the parts made by others inside the app

== What it is

An app for Logic Pro: listen to samples (from Splice or elsewhere), choose one, and load it into
Quick Sampler, a Drum Machine Designer pad, or an audio track (matched to the project tempo and key).
It is designed to be used with VoiceOver and the keyboard only.

== Requirements

- A Mac with Apple silicon (M1 or later), macOS 15 or later (tested by the author on 15.7.8)
- Logic Pro 12.3, displayed in English or Japanese (chapter 2 of the manual)
- VoiceOver
- The app's window and speech follow your Mac's language settings: English or Japanese (chapter 2 of the manual)
- Pasting on audio tracks needs ffmpeg (install it with Homebrew). rubberband improves the sound quality
- The keys used inside Logic (optional) need Keyboard Maestro

== Installing (short version)

1. Move "Logicサンプルローダー.app" from this folder to the Applications folder
2. Open it. The first time, macOS may stop it from opening.
   How to open it is in "3.2 Opening the app for the first time" in the manual
3. The first time you load a sample, you are asked for Accessibility permission.
   Turn it on for the app in System Settings, then open the app again

For details, read "Manual (English).html" (or "Manual (English).txt").
Inside the app, Command-Shift-K reads the list of keyboard commands.

== Made with AI

This software was made with AI (Anthropic's Claude) under the direction and decisions
of the author (Takahashi). Most of the program code and of the text in the manual
and other documents was written by the AI.

== License

MIT License (Copyright (c) 2026 Takahashi). It is free to use, with no warranty.
For details, read "ライセンス.txt" (License).
