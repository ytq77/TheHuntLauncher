# Notices and provenance

BBSC Launcher is an independent community launcher and compatibility patch set.
The Hunt launcher design, integration and compatibility work: iytq (2026).

Private UI font assets: Noto Sans/Serif CJK (notofonts) and LXGW WenKai Screen
(lxgw and upstream contributors), SIL Open Font License 1.1. Full copyright,
reserved-name notices and licenses accompany the unmodified fonts in assets/fonts/.
Artwork in this local desktop candidate was supplied and selected by the user;
source-file hashes are recorded in assets/artwork/provenance.json.

This project's original launcher code and patches are provided under
SPDX-License-Identifier: GPL-2.0-or-later. Derived code retains upstream notices.

- BBSC: Copyright (C) 2026 CLS_AMG and contributors; GPL-2.0-or-later.
  Source: https://github.com/CLSAMG22/BBSC
- BBport: https://github.com/Supermedo/bloodborne_pc
  Its source and bundled shadPS4 components retain their GPL and other notices.
  The trophy catalogue derives from its pinned bbport_trophies.py and
  bbport_lang_trophies.py (official PSN text as identified by upstream).
  Trophy images belong to their respective rights holders, including Sony and
  FromSoftware. The desktop reads them from the user's local game in memory;
  game trophy PNGs are not included in application distributions.
- Some online components carry GPL-3.0 notices. Binary distributions retain these
  notices in `licenses/`, alongside the GPL-2.0-or-later application notices.
- shadPS4 and shadp2p: their respective authors and contributors.
- The server recipe pins Wozzardman/shadnet-p2p and its Protobuf/Abseil
  dependencies, preserves the STUN source notices, and downloads the pinned BBSC
  game service together with its LICENSE, NOTICE, CREDITS and SUPPORTERS files.
  The service also retains LICENSE.NoDreamForHunter (fhody125, MIT).
  Exact source hashes and recipe limitations are documented in
  `docs/server/recipe/sources.json` and `docs/server/recipe/SOURCES.md`.
- FSR4 assets: AMD FidelityFX SDK MIT license; asset builds by FireBurn/Q2RTX,
  pinned by BBport's fetch script. Included assets carry LICENSE-FSR4-v07.txt.
- FSR 4.1.1 Vulkan provider: fsr4vk v0.4.3 with the AMD driver patch; GPLv3
  provider and MIT AMD data. Licenses are in fsr4vk/LICENSES; the verified
  corresponding source archive is distributed beside the application archive.
- AMD FSR/ML-FG DX12 and Microsoft Agility components retain their original
  notices in licenses/; exact upstream SDK versions and SHA256 checks are in
  the pinned BBport packaging scripts.
- DLSS bridge: IFreemz and bbport contributors, MIT. NVIDIA's unmodified
  DLSS SR/FG runtimes retain NVIDIA-DLSS-LICENSE.txt. These five files are
  verified against the fixed official BBport 1.9.4 release; hashes and source
  URL are retained in source-info/DLSS-runtime-source.json and
  scripts/dlss-runtime.lock.json. No NVIDIA runtime is relicensed as GPL.
- MSYS2 runtime DLLs and Python: original licenses are copied into
  `runtime/licenses/`; build package versions are recorded with release artifacts.

The exact upstream commits are in upstream.lock.json. Every release built by this
repository includes TheHuntLauncher-sources.zip with the patched application sources,
generated localization, build scripts, and BBport sources including its submodules.
The matching source archive is distributed beside the binaries.

No game executable, game data, Sony SDK material, save data or private account
configuration is part of this source repository or its generated release archive.
The launcher expects the user to provide their own local installation.
