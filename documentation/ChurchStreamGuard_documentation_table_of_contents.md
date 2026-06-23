# ChurchStreamGuard Documentation Table of Contents

Last reviewed: 2026-06-23

This document is the index for the `documentation` folder of the ChurchStreamGuard repository. The links are **relative links**, so they are intended to work when this file is stored inside the same `documentation` folder as the documents it references.

## How to use this index

Use the **Current operating and rebuild documents** section for normal church AV / streaming work.

Use the **Supplemental setup references** section when you need extra detail on a narrower task such as Windows Task Scheduler.

Use the **Earlier versions and historical reference** section only when comparing old designs, recovering earlier notes, or checking how the system changed over time.

In most cases:

- **PDF** is best for reading, printing, or giving to a volunteer.
- **DOCX** is the editable Microsoft Word source.
- **MD** means Markdown, which GitHub displays as a formatted web document.

---

## Start here

These are the best first documents for a new operator, visiting helper, or future maintainer.

| Resource | Links | What the document offers | Recommended use |
|---|---|---|---|
| Stream Agent III sd User Manual and Reference | [Markdown](./USER_MANUAL_Stream_Agent_III_sd.md) | Main operating reference for Stream Agent III sd. Covers the app workflow, Web HUD, Director page, OBS control, camera/director operation, MIDI cues, timer/start/stop support, configuration, fallback operation, and service-day use. | Start here for day-to-day operation of Stream Agent III sd and for understanding how the automation layer fits the church AV system. |
| Church AVTECH Network Diagram — Stream Agent III sd, Axis Audio, No Pi Helper | [PDF](./church_avtech_network_diagram_stream_agent_III_sd_axis_audio_no_pi_helper.pdf) | Current system-level network and signal-flow diagram after the move to Axis embedded audio and after removing the Raspberry Pi helper from the documented design. | Use before troubleshooting or rebuilding so the physical devices, network paths, and audio/video relationships are clear. |

---

## Current operating and rebuild documents

These are the main current guides to use when rebuilding, checking, or operating the church streaming system.

| Resource | Links | What the document offers | Recommended use |
|---|---|---|---|
| OBS Studio Setup and Rebuild Guide v2 — Axis Audio / NHLC | [PDF](./AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide_v2_axis_audio_NHLC.pdf)<br>[DOCX](./AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide_v2_axis_audio_NHLC.docx) | Current OBS Studio rebuild guide for the New Hope Lutheran Church Axis-audio arrangement. Documents OBS scenes, sources, profile/streaming setup, WebSocket requirements, audio source arrangement, and recovery/rebuild steps. | Use as the primary OBS rebuild and verification document. |
| Proclaim Programming Setup and Rebuild Guide v2 | [PDF](./AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide_v2.pdf)<br>[DOCX](./AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide_v2.docx) | Current Proclaim setup/rebuild guide. Covers Proclaim service programming, MIDI cue programming, slide/service preparation, transparent livestream output concepts, and how Proclaim supports Stream Agent actions. | Use when setting up Proclaim cues, preparing services, or rebuilding the Proclaim side of the streaming workflow. |
| YouTube Live Interface Setup and Rebuild Guide v2 — NHLC Profile | [PDF](./AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide_v2_NHLC_profile.pdf)<br>[DOCX](./AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide_v2_NHLC_profile.docx) | Current YouTube Live setup/rebuild guide for the NHLC profile. Covers YouTube Studio access, scheduled/live-stream settings, stream profile checks, stream-start readiness, and emergency verification steps. | Use before Sunday service to confirm YouTube-side setup and when rebuilding the YouTube Live configuration. |
| DL32SE Mixer / Master Fader SE Guide — Axis Audio and PC Playback Revision | [PDF](./AV_Tech_DL32SE_Mixer_Master_Fader_Guide_revised_axis_audio_pc_playback.pdf)<br>[DOCX](./AV_Tech_DL32SE_Mixer_Master_Fader_Guide_revised_axis_audio_pc_playback.docx) | Current mixer reference for the Mackie DL32SE and Master Fader SE. Covers the mixer’s role, main sanctuary sound, stream audio path, Axis embedded audio relationship to OBS, optional future USB/ASIO paths, Master Fader tablet control, aux outputs, and playing PC app sound into the mixer through USB returns. | Use for mixer setup, Master Fader operation, sanctuary sound, stream mix understanding, and PC/Proclaim playback routing into the DL32SE. |
| ER605 Router and PoE Guide — NHLC Corrected | [PDF](./AV_Tech_ER605_Router_and_PoE_Guide_NHLC_corrected.pdf)<br>[DOCX](./AV_Tech_ER605_Router_and_PoE_Guide_NHLC_corrected.docx) | Corrected NHLC router, PoE, and AV network guide. Covers the ER605 router, PoE/network infrastructure, church AV LAN arrangement, addressing, and related rebuild notes. | Use as the main readable router/PoE guide unless the final DOCX is later confirmed and exported as the current PDF. |
| ER605 Router and PoE Guide — Final Editable Source | [DOCX](./AV_Tech_ER605_Router_and_PoE_Guide_final.docx) | Editable final-source version of the ER605/PoE guide. The filename suggests it may be newer than the corrected pair, but there is no matching final PDF in this folder. | Keep as the likely latest editable source. Confirm against the corrected PDF before archiving either file. |
| AutoLaunch Task Scheduler Guide v2 | [PDF](./AV_Tech_AutoLaunch_Task_Scheduler_Guide_v2.pdf)<br>[DOCX](./AV_Tech_AutoLaunch_Task_Scheduler_Guide_v2.docx) | Current Windows Task Scheduler / auto-launch guide. Covers how Stream Agent, OBS, or related helper programs are launched automatically on the streaming PC. | Use when rebuilding startup automation or checking that Sunday-service software starts correctly. |

---

## Supplemental setup references

These documents are useful supporting references. Some may overlap with the main current guides.

| Resource | Links | What the document offers | Recommended use |
|---|---|---|---|
| Stream Agent III Task Scheduler AutoLaunch — With Screenshots | [DOCX](./Stream_Agent_III_Task_Scheduler_AutoLaunch_with_Screenshots.docx) | Screenshot-based Task Scheduler setup reference for Stream Agent III auto-launch. | Use when a visual step-by-step Task Scheduler reference is easier than the general AutoLaunch guide. |
| Documentation Table of Contents — Editable/Printable Copy | [DOCX](./ChurchStreamGuard_documentation_table_of_contents.docx) | Word version of this documentation index, useful if a printable or editable copy is wanted outside GitHub. | Optional. The Markdown version is better for GitHub display. |
| Documentation Table of Contents — GitHub Markdown Version | [Markdown](./ChurchStreamGuard_documentation_table_of_contents.md) | The GitHub-friendly documentation index. | This is the file you are reading. It can also be copied or renamed to `README.md` if you want GitHub to display it automatically when opening the `documentation` folder. |

---

## Earlier versions and historical reference

These files appear to be older versions retained for comparison and history. They may still be useful, but the v2, NHLC, corrected, revised, or final files should normally be preferred.

| Resource | Links | What the document offers | Recommended use |
|---|---|---|---|
| OBS Studio Setup and Rebuild Guide — Earlier Version | [PDF](./AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide.pdf)<br>[DOCX](./AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide.docx) | Earlier OBS setup/rebuild guide, likely before the v2 Axis-audio/NHLC update. | Use only for history or comparison against the current v2 OBS guide. |
| Proclaim Programming Setup and Rebuild Guide — Earlier Version | [PDF](./AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide.pdf)<br>[DOCX](./AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide.docx) | Earlier Proclaim programming/rebuild guide, retained alongside the current v2 version. | Use only for history or comparison against the current v2 Proclaim guide. |
| YouTube Live Interface Setup and Rebuild Guide — Earlier Version | [PDF](./AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide.pdf)<br>[DOCX](./AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide.docx) | Earlier YouTube Live setup/rebuild guide, retained alongside the current NHLC-profile v2 version. | Use only for history or comparison against the current v2 NHLC-profile YouTube guide. |
| ER605 Router and PoE Guide — Earlier Version | [PDF](./AV_Tech_ER605_Router_and_PoE_Guide.pdf) | Earlier ER605/PoE/network guide, retained alongside corrected and final-source files. | Use only for history or comparison. |
| Stream Agent III Task Scheduler AutoLaunch — Earlier/Simple Version | [DOCX](./Stream_Agent_III_Task_Scheduler_AutoLaunch.docx) | Earlier or simpler Task Scheduler auto-launch reference for Stream Agent III. | Use only if the screenshot version or v2 AutoLaunch guide does not answer a specific setup question. |
| Church AVTECH Network Diagram — Earlier Version | [PDF](./church_avtech_network_diagram_stream_agent_III.pdf) | Earlier network/system diagram before the later Axis-audio/no-Pi-helper documentation update. | Use only for history or when comparing the previous design to the current documented design. |

---

## Suggested reading order for a full rebuild

1. [Church AVTECH Network Diagram — current Axis audio / no Pi helper version](./church_avtech_network_diagram_stream_agent_III_sd_axis_audio_no_pi_helper.pdf)
2. [OBS Studio Setup and Rebuild Guide v2 — Axis Audio / NHLC](./AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide_v2_axis_audio_NHLC.pdf)
3. [DL32SE Mixer / Master Fader SE Guide — Axis Audio and PC Playback Revision](./AV_Tech_DL32SE_Mixer_Master_Fader_Guide_revised_axis_audio_pc_playback.pdf)
4. [Proclaim Programming Setup and Rebuild Guide v2](./AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide_v2.pdf)
5. [YouTube Live Interface Setup and Rebuild Guide v2 — NHLC Profile](./AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide_v2_NHLC_profile.pdf)
6. [Stream Agent III sd User Manual and Reference](./USER_MANUAL_Stream_Agent_III_sd.md)
7. [AutoLaunch Task Scheduler Guide v2](./AV_Tech_AutoLaunch_Task_Scheduler_Guide_v2.pdf)

---

## Suggested reading order for Sunday service checking

1. [Stream Agent III sd User Manual and Reference](./USER_MANUAL_Stream_Agent_III_sd.md)
2. [OBS Studio Setup and Rebuild Guide v2 — Axis Audio / NHLC](./AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide_v2_axis_audio_NHLC.pdf)
3. [YouTube Live Interface Setup and Rebuild Guide v2 — NHLC Profile](./AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide_v2_NHLC_profile.pdf)
4. [DL32SE Mixer / Master Fader SE Guide — Axis Audio and PC Playback Revision](./AV_Tech_DL32SE_Mixer_Master_Fader_Guide_revised_axis_audio_pc_playback.pdf)
5. [Proclaim Programming Setup and Rebuild Guide v2](./AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide_v2.pdf)

---

## Maintenance notes for this index

- When a new guide is added, place it in the correct section and add a short description of what it offers.
- When a guide is superseded, move the older guide to **Earlier versions and historical reference** rather than deleting it immediately.
- If a DOCX file is updated, regenerate or update the matching PDF so readers have a stable read/print version.
- If this file is renamed to `README.md`, GitHub will display it automatically when the `documentation` folder is opened.
- The DL32SE / Master Fader SE guide now points to the revised `axis_audio_pc_playback` file pair. Older `AV_Tech_DL32SE_Mixer_Master_Fader_Guide.pdf` and `.docx` names should not be used unless they are intentionally restored to the folder.
- The ER605 files currently include `NHLC_corrected` and `final` names. Confirm which one is authoritative before removing either version.
