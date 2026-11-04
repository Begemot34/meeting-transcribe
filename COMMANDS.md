**English** | [Русский](COMMANDS.ru.md)

# Commands

| Command | What it does |
| --- | --- |
| [`auto`](#auto) | the whole pipeline in one go: recognition, speakers, screenshots, names, summary, HTML and PDF |
| [`asr`](#asr) | speech recognition and speaker diarization, in parallel |
| [`frames`](#frames) | screenshot candidates and contact sheets to review them |
| [`badges`](#badges) | crops of the "who is speaking" badge, per speaker |
| [`strip`](#strip) | one badge crop per second for a disputed fragment |
| [`merge`](#merge) | build `transcript.md` and `screenshots/` from the work folder |
| [`summarize`](#summarize) | add a summary and rebuild HTML and PDF |
| [`export`](#export) | rebuild HTML and PDF from `transcript.md`, e.g. after manual edits |
| [`setup`](#setup) | install the environment and download the models |
| `doctor [--language X]` | show what is installed and where the models are; installs nothing |
| `install-skill` / `uninstall-skill` | register [SKILL.md](SKILL.md) for agents / remove it |

`./transcribe help` prints the same list. `auto` is enough for most recordings; the other commands are its
steps, for when you need to redo one of them by hand or when an agent works through [SKILL.md](SKILL.md).
The `MT_*` variables in the defaults are described in [SETTINGS.md](SETTINGS.md).

## auto

```text
./transcribe auto <video> <out_dir> [options]
```

| Option | Default | What it does |
| --- | --- | --- |
| `--title T` | file name | title in the transcript |
| `--date YYYY-MM-DD` | today | date in the transcript |
| `--language X` | `MT_LANGUAGE` | meeting language |
| `--summary B` | from `setup` | who writes the summary for this run: `claude`, `codex`, `opencode`, `local`, `api` |
| `--no-summary` | — | no summary for this run |
| `--no-names` | — | keep `SPEAKER_xx`: no names from the screen and the text |

The intermediate files are in `<out_dir>/.work`: the steps below can be rerun on it.

## Steps

### asr

```text
./transcribe asr <video> <work> [options]
```

| Option | Default | What it does |
| --- | --- | --- |
| `--language X` | `MT_LANGUAGE` | meeting language |
| `--max-speakers N` | `10` | upper bound on the number of speakers |

### frames

```text
./transcribe frames <video> <work> [options]
```

| Option | Default | What it does |
| --- | --- | --- |
| `--threshold X` | `0.3` | sensitivity to frame changes; `0.08` if a document was scrolled |
| `--min-gap S` | `15` | minimum seconds between screenshots |

### badges

```text
./transcribe badges <video> <work> [options]
```

| Option | Default | What it does |
| --- | --- | --- |
| `--crop W:H:X:Y` | `360:76:1240:0` | where the "who is speaking" badge is in the frame |
| `--share-start S` | `70` | second from which the screen is shared |
| `--per-speaker N` | `6` | badge crops per speaker |
| `--probe T` | — | only save the full frame at second T, to pick `--crop` |

### strip

```text
./transcribe strip <video> <work> <start_s> [options]
```

| Option | Default | What it does |
| --- | --- | --- |
| `--duration S` | `40` | length of the strip in seconds, from `start_s` |
| `--crop W:H:X:Y` | `360:76:1240:0` | where the badge is in the frame |

### merge

```text
./transcribe merge <work> <out_dir> --title T --date D [options]
```

| Option | Default | What it does |
| --- | --- | --- |
| `--title T`, `--date D` | required | title and date in the transcript |
| `--speakers file.json` | — | speaker names: `{"SPEAKER_00": "Anna"}` |
| `--language X` | recognized language | language of the headings in the result |
| `--merge-gap S` | `1.5` | join a speaker's utterances separated by a pause shorter than S seconds |
| `--raw-speakers` | — | keep diarization labels as they are: no smoothing of flickers, no boundary fix |

### summarize

```text
./transcribe summarize <out_dir> --llm B [options]
```

| Option | Default | What it does |
| --- | --- | --- |
| `--llm B` | `MT_LLM` | who writes the summary: `claude`, `codex`, `opencode`, `local`, `api` ([Summary](README.md#summary)) |
| `--model M` | the CLI's default; for `opencode` — `MT_OPENCODE_MODEL` | model for the summary |

### export

```text
./transcribe export <out_dir> [options]
```

| Option | Default | What it does |
| --- | --- | --- |
| `--no-pdf` | — | HTML only |
| `--width W` | `1280` | width of the screenshots in the HTML, px |
| `--quality Q` | `72` | JPEG quality of the screenshots in the HTML |

## setup

```text
./transcribe setup [options]
```

Without options it asks questions; any option turns the questions off, and what is not given takes its default.

| Option | Default | What it does |
| --- | --- | --- |
| `--language en,ru` | `en,ru` | languages to download models for; the first one becomes `MT_LANGUAGE` |
| `--model M` | `mlx-large-v3-turbo` on Apple Silicon, else `large-v3` | recognition model, see `MT_WHISPER_MODEL` |
| `--device cpu\|cuda` | saved one, else `cpu` | `cuda`: recognition and diarization on an NVIDIA GPU (Linux) |
| `--summary B` | saved one is kept | default summary backend: `claude`, `codex`, `opencode`, `local`, `api`, `none` |
| `--skill` / `--no-skill` | `--no-skill` | run `install-skill` or not |
| `--yes`, `-y` | — | accept the defaults without questions |
