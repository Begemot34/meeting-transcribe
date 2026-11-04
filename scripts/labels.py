"""Words that end up in the result (transcript.md, HTML, summary) — in the meeting language.

Languages without an entry get English. merge.py writes these headings, export.py and summarize.py find them again,
so the parsers detect the language by the transcript heading (see detect)."""
import re

LABELS = {
    "en": {
        "transcript": "Transcript", "summary": "Summary", "date": "Date", "duration": "Duration",
        "utterances": "Utterances", "screenshots": "Screenshots", "speakers": "Speakers", "min": "min",
        "screen_at": "Screen at", "screenshot": "Screenshot", "made_with": "Made with",
        "no_summary": "Transcript only, no summary yet. To add a summary (decisions, next steps, topics) to this file, run:",
        "headings": "## Summary / ### Decisions / ### Next steps / ### Open questions and risks / "
                    "### Discussion by topic",
        "unassigned": "Unassigned", "no_deadline": "no deadline",
    },
    "ru": {
        "transcript": "Транскрипт", "summary": "Саммари", "date": "Дата", "duration": "Длительность",
        "utterances": "Реплик", "screenshots": "Скриншотов", "speakers": "Спикеры", "min": "мин",
        "screen_at": "Экран в", "screenshot": "Скриншот", "made_with": "Сделано в",
        "no_summary": "Это транскрипт без саммари. Чтобы добавить в этот файл саммари (решения, шаги, темы), выполни:",
        "headings": "## Саммари / ### Решения / ### Следующие шаги / ### Открытые вопросы и риски / "
                    "### Обсуждение по темам",
        "unassigned": "Не назначено", "no_deadline": "срок не назван",
    },
}


def labels(language):
    return LABELS.get(language, LABELS["en"])


MARKER = re.compile(r"^<!-- meeting-transcribe language=([\w-]+) -->$", re.M)


def marker(language):
    """Hidden line in transcript.md with the meeting language: headings alone cannot tell German from English."""
    return f"<!-- meeting-transcribe language={language} -->"


def detect(md):
    """Language of the headings of a transcript.md written by merge.py (a key of LABELS)."""
    lang = meeting_language(md)
    if lang in LABELS:
        return lang
    lines = {ln.strip() for ln in md.splitlines()}
    return next((lang for lang, lb in LABELS.items() if f"## {lb['transcript']}" in lines), "en")


def meeting_language(md):
    """Language the meeting was held in: from the marker, or (older files) from the headings."""
    m = MARKER.search(md)
    if m:
        return m.group(1)
    lines = {ln.strip() for ln in md.splitlines()}
    return next((lang for lang, lb in LABELS.items() if f"## {lb['transcript']}" in lines), "en")


def fmt_time(t):
    """Seconds → MM:SS, or H:MM:SS from one hour on."""
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


def to_sec(t):
    """MM:SS or H:MM:SS → seconds."""
    sec = 0
    for part in t.split(":"):
        sec = sec * 60 + int(part)
    return sec
