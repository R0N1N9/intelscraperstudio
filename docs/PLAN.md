# Plan

## Goal

Shoot, then plug in the card. Everything after that is automated up to one approval tap:

- import and back up the footage
- cull the clips
- color-correct log footage
- cut Shorts, Reels and YouTube videos to music
- write titles and captions
- write a blog post
- post on a schedule

## Decisions so far

| Topic | Decision |
|---|---|
| Hardware | Windows PC with an NVIDIA GPU. Footage from a Sony A7C (S-Log3) and a DJI Mavic 3 (D-Log M). |
| Outputs | YouTube Shorts first, then Instagram Reels, full YouTube videos, and a blog/portfolio. |
| Posting | Always ask before posting. |
| Look | No presets yet. Start from a chosen look and learn from the user's adjustments. |
| Audience | Personal tool first; possibly a product later. |
| Name | intelscraperstudio |
| Design | Minimal graphite UI with one amber accent and quiet tactical touches: topographic background, coordinates, viewfinder marks, and an aperture-iris logo. The clickable mockup is in [`design/mockup.html`](design/mockup.html). |

## Architecture

- **Engine:** Python, running on the user's PC.
  - FFmpeg for decode, encode and color. Uses the NVIDIA GPU via hardware decode and NVENC.
  - OpenCV for clip analysis and librosa for music analysis.
  - SQLite for the catalog.
- **Interface:** a local web UI served by the engine at `127.0.0.1`.
  - It is plain HTML, CSS and JS with no build step.
  - It can later be wrapped in Tauri as a native Windows app without changing the UI.
- **AI writing (step 4):** the Claude API, given frames from the footage and the shoot's metadata, for titles, captions and blog drafts.

## Steps

### 1. Import, review and music library: done

- [x] Card detection, verified copy to library and backup, dedupe, shoot grouping
- [x] Metadata from ffprobe, Sony XML and DJI SRT (camera, log profile, GPS, exposure)
- [x] Clip scoring (focus, steadiness, light), verdicts with reasons, best moment, near-duplicates
- [x] Thumbnails and 540p preview files (NVENC when available)
- [x] Review screen with keyboard culling and user overrides
- [x] Music folder import: tempo, beats, octave correction, energy, sections, estimated vibe
- [x] Settings: library and backup folders, verify copies, previews, shoot gap
- [x] Test suite on synthetic cards and songs

### 2. Cutting room: next

- [ ] Log to Rec.709 conversion: Sony S-Log3/S-Gamut3.Cine LUT, DJI D-Log M LUT
- [ ] Looks (Natural, Golden, Moody, Soft film) on top, learning from adjustments
- [ ] Cut-to-music engine:
  - cuts land on the song's beats and section changes
  - cut-length modes: dynamic (default), longer holds, short cuts
  - target length, or match the song
- [ ] Edit sets: same vibe with different songs, or one edit per vibe
- [ ] Vertical reframing (subject tracking), stabilization, title and captions
- [ ] Render with NVENC: 9:16 Short/Reel and 16:9 full video

### 3. Approval and publishing

- [ ] Approve-posts screen with platform previews
- [ ] YouTube Data API upload and scheduling
- [ ] Instagram Graph API. Needs a free Creator or Business account.
- [ ] Posting schedule with suggested times

### 4. Writing and blog

- [ ] Titles, descriptions, hashtags and blog drafts via the Claude API, in the user's caption voice
- [ ] Blog/portfolio publishing (WordPress, Ghost, or a generated static site)
- [ ] Photo culling for ARW/DNG/JPG

## Known limits in step 1

- **Scoring thresholds** are tuned on synthetic test clips. They will need a pass on real A7C and Mavic 3 footage.
- **Vibe estimates** are rule-based. Correcting a song's vibe in the Music library is the intended workflow.
- **Tempo:** a very slow song (about 62 BPM or less) can still read at double speed. Use the ½× button.
- **Timestamps:** DJI clips store local clock time without a timezone, so the PC's timezone is used to place them on the timeline.
