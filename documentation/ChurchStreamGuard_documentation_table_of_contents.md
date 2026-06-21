# ChurchStreamGuard Documentation Table of Contents

This document is intended to be saved in the `documentation` folder, preferably as `README.md` or `DOCUMENTATION_TABLE_OF_CONTENTS.md`. The links below are relative links, so they should work correctly when viewed on GitHub from inside the `documentation` folder.

## How to Use This Index

- Use the **Current Setup and Rebuild Guides** section for normal rebuilding and verification work.
- Use **Start Here** if you are new to the system or need the overall operating picture.
- Use **Earlier Versions and Historical Reference** only when comparing changes or recovering information not yet carried into the newer documents.
- In most cases, the **PDF** file is best for reading or printing, and the **DOCX** file is the editable source document.

## Start Here

These are the best first documents for a new operator, a visiting helper, or anyone trying to understand the whole system.

| Resource | Links | What the document offers | Recommended use |
|---|---|---|---|
| Stream Agent III sd User Manual and Reference | [Markdown](./USER_MANUAL_Stream_Agent_III_sd.md) | Main operating reference for Stream Agent III sd. Explains the Axis-only design, OBS/Proclaim coordination, Web HUD, Director page, camera presets, MIDI notes, audio controls, intro sequence, timer auto-start, config editor, and fallback operation. | Start here for day-to-day operation and troubleshooting of the Stream Agent application. |
| Church AVTECH Network Diagram — Stream Agent III sd, Axis Audio, No Pi Helper | [PDF](./church_avtech_network_diagram_stream_agent_III_sd_axis_audio_no_pi_helper.pdf) | System-level network and signal-flow diagram showing the AVTECH/church streaming layout after the change to Axis audio and removal of the Raspberry Pi WoL helper reference. | Use before rebuilding or troubleshooting, so the physical and network relationships are clear. |

## Current Setup and Rebuild Guides

These are the main current guides to use when rebuilding or checking the church streaming system.

| Resource | Links | What the document offers | Recommended use |
|---|---|---|---|
| OBS Studio Setup and Rebuild Guide v2 — Axis Audio / NHLC | [PDF](./AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide_v2_axis_audio_NHLC.pdf)<br>[DOCX](./AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide_v2_axis_audio_NHLC.docx) | Current OBS Studio rebuild guide for the NHLC Axis-audio arrangement. Intended to document OBS scenes, sources, profile/streaming setup, WebSocket requirements, audio source arrangement, and recovery/rebuild steps. | Use this as the primary OBS rebuild and verification document. |
| Proclaim Programming Setup and Rebuild Guide v2 | [PDF](./AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide_v2.pdf)<br>[DOCX](./AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide_v2.docx) | Current Proclaim setup/rebuild guide. Intended to document the Proclaim service-programming method, MIDI cue programming, slide/service preparation, and how Proclaim drives Stream Agent actions. | Use this when setting up Proclaim cues or rebuilding Proclaim-related streaming control. |
| YouTube Live Interface Setup and Rebuild Guide v2 — NHLC Profile | [PDF](./AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide_v2_NHLC_profile.pdf)<br>[DOCX](./AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide_v2_NHLC_profile.docx) | Current YouTube Live setup/rebuild guide for the NHLC profile. Intended to document YouTube Studio access, scheduled/live-stream settings, stream profile checks, and emergency verification steps. | Use this before Sunday service to confirm YouTube-side setup and when rebuilding the YouTube Live configuration. |
| DL32SE Mixer / Master Fader Guide | [PDF](./AV_Tech_DL32SE_Mixer_Master_Fader_Guide.pdf)<br>[DOCX](./AV_Tech_DL32SE_Mixer_Master_Fader_Guide.docx) | Mixer reference for the Mackie DL32SE and Master Fader workflow. Intended to document mixer connection, routing, control, snapshots/presets, and streaming-audio considerations. | Use for mixer setup, checking Master Fader operation, and understanding the audio side of the stream. |
| ER605 Router and PoE Guide — NHLC Corrected | [PDF](./AV_Tech_ER605_Router_and_PoE_Guide_NHLC_corrected.pdf)<br>[DOCX](./AV_Tech_ER605_Router_and_PoE_Guide_NHLC_corrected.docx) | Corrected NHLC router/PoE/network guide. Intended to document the ER605 router, PoE/network infrastructure, church AV LAN arrangement, addressing, and related rebuild notes. | Use as the main readable router/PoE guide unless a newer final PDF is added. |
| ER605 Router and PoE Guide — Final Editable Source | [DOCX](./AV_Tech_ER605_Router_and_PoE_Guide_final.docx) | Editable final-source version of the ER605/PoE guide. The filename suggests it may be newer than the corrected pair, but there is no matching final PDF in the folder. | Keep this as the likely latest editable source. Confirm against the corrected PDF before archiving either file. |
| AutoLaunch Task Scheduler Guide v2 | [PDF](./AV_Tech_AutoLaunch_Task_Scheduler_Guide_v2.pdf)<br>[DOCX](./AV_Tech_AutoLaunch_Task_Scheduler_Guide_v2.docx) | Current Windows Task Scheduler / auto-launch guide. Intended to document how Stream Agent, OBS, or related helper programs are launched automatically on the streaming PC. | Use when rebuilding startup automation or checking that Sunday-service software starts correctly. |
| Stream Agent III Task Scheduler AutoLaunch — With Screenshots | [DOCX](./Stream_Agent_III_Task_Scheduler_AutoLaunch_with_Screenshots.docx) | Screenshot-based Task Scheduler setup reference for Stream Agent III auto-launch. | Use when a visual step-by-step Task Scheduler reference is easier than the general AutoLaunch guide. |

## Earlier Versions and Historical Reference

These files appear to be older versions retained for reference. They may still be useful for comparing changes, but the v2, NHLC, corrected, or final files should normally be preferred.

| Resource | Links | What the document offers | Recommended use |
|---|---|---|---|
| OBS Studio Setup and Rebuild Guide — Earlier Version | [PDF](./AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide.pdf)<br>[DOCX](./AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide.docx) | Earlier OBS setup/rebuild guide, likely before the v2 Axis-audio/NHLC update. | Use only for history or comparison against the current v2 OBS guide. |
| Proclaim Programming Setup and Rebuild Guide — Earlier Version | [PDF](./AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide.pdf)<br>[DOCX](./AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide.docx) | Earlier Proclaim programming/rebuild guide, retained alongside the current v2 version. | Use only for history or comparison against the current v2 Proclaim guide. |
| YouTube Live Interface Setup and Rebuild Guide — Earlier Version | [PDF](./AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide.pdf)<br>[DOCX](./AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide.docx) | Earlier YouTube Live setup/rebuild guide, retained alongside the current NHLC-profile v2 version. | Use only for history or comparison against the current v2 NHLC-profile YouTube guide. |
| ER605 Router and PoE Guide — Earlier Version | [PDF](./AV_Tech_ER605_Router_and_PoE_Guide.pdf) | Earlier ER605/PoE/network guide, retained alongside corrected and final-source files. | Use only for history or comparison. |
| Stream Agent III Task Scheduler AutoLaunch — Earlier/Simple Version | [DOCX](./Stream_Agent_III_Task_Scheduler_AutoLaunch.docx) | Earlier or simpler Task Scheduler auto-launch reference for Stream Agent III. | Use only if the screenshot version or v2 AutoLaunch guide does not answer a specific setup question. |

## Suggested Reading Order for a Rebuild

1. [Church AVTECH Network Diagram](./church_avtech_network_diagram_stream_agent_III_sd_axis_audio_no_pi_helper.pdf)
2. [OBS Studio Setup and Rebuild Guide v2](./AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide_v2_axis_audio_NHLC.pdf)
3. [DL32SE Mixer / Master Fader Guide](./AV_Tech_DL32SE_Mixer_Master_Fader_Guide.pdf)
4. [Proclaim Programming Setup and Rebuild Guide v2](./AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide_v2.pdf)
5. [YouTube Live Interface Setup and Rebuild Guide v2](./AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide_v2_NHLC_profile.pdf)
6. [Stream Agent III sd User Manual](./USER_MANUAL_Stream_Agent_III_sd.md)
7. [AutoLaunch Task Scheduler Guide v2](./AV_Tech_AutoLaunch_Task_Scheduler_Guide_v2.pdf)

## Maintenance Notes

- When a new guide is added, place it in the correct section above and add a one-sentence description of what it offers.
- When a guide is superseded, move the older guide to **Earlier Versions and Historical Reference** rather than deleting it immediately.
- If a DOCX is updated, regenerate or update the matching PDF so readers have a stable print/read version.
- The ER605 files currently include `NHLC_corrected` and `final` names. Confirm which one is authoritative before removing either version.
