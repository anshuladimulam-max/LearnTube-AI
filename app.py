import streamlit as st
import pandas as pd
import re
import random
import json
import uuid
import hashlib
import html
import requests
from datetime import datetime, timezone

st.set_page_config(page_title="LearnTube AI", page_icon="🎓", layout="wide")

# -------------------- Persistent GitHub analytics --------------------
# Configure these values in Streamlit Community Cloud > App settings > Secrets:
# GITHUB_TOKEN = "github_pat_..."
# GITHUB_REPO = "anshuladimulam-max/LearnTube-AI"
# GITHUB_BRANCH = "main"
# ANALYTICS_FILE = "data/quiz_history.json"

GITHUB_API = "https://api.github.com"
DEFAULT_REPO = "anshuladimulam-max/LearnTube-AI"
DEFAULT_BRANCH = "main"
DEFAULT_ANALYTICS_FILE = "data/quiz_history.json"


def get_setting(name, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def github_config():
    token = get_setting("GITHUB_TOKEN")
    repo = get_setting("GITHUB_REPO", DEFAULT_REPO)
    branch = get_setting("GITHUB_BRANCH", DEFAULT_BRANCH)
    file_path = get_setting("ANALYTICS_FILE", DEFAULT_ANALYTICS_FILE)
    if not token:
        raise RuntimeError(
            "GitHub storage is not configured. Add GITHUB_TOKEN to Streamlit Secrets."
        )
    return str(token), str(repo), str(branch), str(file_path)


def github_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def load_history():
    token, repo, branch, file_path = github_config()
    url = f"{GITHUB_API}/repos/{repo}/contents/{file_path}"
    response = requests.get(
        url,
        headers=github_headers(token),
        params={"ref": branch},
        timeout=20,
    )
    if response.status_code == 404:
        return [], None
    if response.status_code != 200:
        raise RuntimeError(
            f"GitHub could not load analytics (HTTP {response.status_code}): "
            f"{response.text[:300]}"
        )
    payload = response.json()
    import base64
    raw = base64.b64decode(payload["content"]).decode("utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The GitHub analytics JSON file is invalid.") from exc
    if not isinstance(data, list):
        raise RuntimeError("The GitHub analytics file must contain a JSON list.")
    return data, payload.get("sha")


def save_attempt(attempt):
    token, repo, branch, file_path = github_config()
    headers = github_headers(token)
    url = f"{GITHUB_API}/repos/{repo}/contents/{file_path}"

    # Read latest data immediately before writing to avoid overwriting history
    # loaded earlier in the user's Streamlit session.
    latest, sha = load_history()
    if any(item.get("attempt_id") == attempt["attempt_id"] for item in latest):
        return latest

    latest.append(attempt)
    import base64
    content = json.dumps(latest, ensure_ascii=False, indent=2)
    body = {
        "message": f"Save LearnTube quiz attempt {attempt['attempt_id']}",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    response = requests.put(url, headers=headers, json=body, timeout=25)
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"GitHub could not save analytics (HTTP {response.status_code}): "
            f"{response.text[:400]}"
        )
    return latest


# -------------------- Learning and quiz helpers --------------------
# A small seed vocabulary is retained as a fallback, but topic detection is
# transcript-driven so the app is not restricted to machine-learning videos.
CONCEPTS = [
    "Artificial Intelligence", "Machine Learning", "Deep Learning",
    "Data Science", "Neural Networks", "Linear Regression",
    "Classification", "Clustering", "Natural Language Processing",
    "Computer Vision", "Supervised Learning", "Unsupervised Learning"
]

QUIZ_QUESTION_COUNT = 5

_STOPWORDS = set("""a an the and or but if then else when while where what which who whom
whose why how is am are was were be been being do does did have has had can could
may might must shall should will would to of in on at by for from with about into
onto over under after before between through during as it its this that these those
i you he she we they them their his her our your my me us not no yes so than too
very also just only more most much many some any each every all both few other such
same own up down out off again further once here there then because until unless
than like get got make made use used using one two three first second new old
thing things way ways example examples people person time times video videos
lesson lessons topic topics today really want going go went come came say says said
know think thought see seen look looking learn learning learned understand
understanding show shows shown explain explained explanation""".split())

# Frequent verbs and generic narration words are poor topic labels.
_VERB_NOISE = set("""
protects protect changed change affects affect influence influences began begin
increased increase grew grow uses use produces produce produced makes made powered
power explains explain describe describes occurs occur absorbs absorb depends depend
attracts attract monitor monitors verifies verify helps help states state discusses
discuss includes include called call known know means mean shows show tells tell
works work becomes become provides provide contains contain requires require
looks look gets get gives give uses using
""".split())


def identify_concepts(text, limit=18):
    """Extract recurring words and short phrases from varied subject domains.

    This is statistical keyphrase extraction, not semantic AI. A fallback to
    frequent content words prevents long, valid transcripts returning zero
    topics merely because the same two-word phrase is not repeated.
    """
    if not text or not text.strip():
        return []

    sentences = re.split(r"(?<=[.!?])\s+|[\r\n]+", text)
    phrase_counts, phrase_display = {}, {}
    word_counts, word_display = {}, {}

    for sentence in sentences:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#'-]*", sentence)
        normalized = [t.strip("'-").lower() for t in tokens]
        originals = [t.strip("'-") for t in tokens]
        for word, original in zip(normalized, originals):
            if len(word) >= 3 and word not in _STOPWORDS and word not in _VERB_NOISE and not word.isdigit():
                word_counts[word] = word_counts.get(word, 0) + 1
                word_display.setdefault(word, original)
        for n in (2, 3):
            for i in range(len(normalized) - n + 1):
                gram = normalized[i:i+n]
                if any(w in _STOPWORDS or len(w) < 3 for w in gram):
                    continue
                key = " ".join(gram)
                phrase_counts[key] = phrase_counts.get(key, 0) + 1
                phrase_display.setdefault(key, " ".join(originals[i:i+n]))

    ranked = []
    for phrase, count in phrase_counts.items():
        if count >= 2:
            score = count * (1.0 + 0.65 * (len(phrase.split()) - 1))
            ranked.append((score, count, phrase, phrase_display[phrase]))

    # Include frequent single terms as a fallback and as useful distractors.
    for word, count in word_counts.items():
        if count >= 2:
            ranked.append((count * 0.9, count, word, word_display[word]))

    ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
    results, seen = [], set()
    for _, _, key, label in ranked:
        norm = key.casefold()
        if norm in seen:
            continue
        # Suppress a single word if it is already contained in a phrase.
        if len(key.split()) == 1 and any(key in existing.split() for existing in seen):
            continue
        results.append(label)
        seen.add(norm)
        if len(results) >= limit:
            break

    # Fill remaining slots with source-specific content words, including
    # single-occurrence nouns. This helps short videos with varied vocabulary.
    for word, count in sorted(word_counts.items(), key=lambda x: (-x[1], x[0])):
        if word in seen or word in _STOPWORDS or word in _VERB_NOISE:
            continue
        if any(word in existing.split() for existing in seen):
            continue
        results.append(word_display[word])
        seen.add(word)
        if len(results) >= min(limit, 12):
            break

    # Keep canonical ML terms when actually present in the transcript.
    for known in CONCEPTS:
        if _concept_regex(known).search(text) and known.casefold() not in seen:
            results.append(known)
            seen.add(known.casefold())
            if len(results) >= limit:
                break
    return results[:limit]

def clean_transcript(text):
    """Normalize pasted, TXT, SRT, and VTT transcript text."""
    if not text:
        return ""
    text = str(text).replace("\ufeff", " ")
    cleaned_lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper() == "WEBVTT" or line.startswith("NOTE "):
            continue
        if re.fullmatch(r"\d+", line):
            continue
        # SRT/VTT timestamps, with or without cue settings.
        if re.match(r"^(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->\s+", line):
            continue
        line = re.sub(r"<[^>]+>", " ", line)
        line = re.sub(r"\[[^\]]{1,40}\]", " ", line)
        cleaned_lines.append(line)
    return re.sub(r"\s+", " ", " ".join(cleaned_lines)).strip()


def split_transcript(text, size=100):
    words = text.split()
    return [" ".join(words[i:i + size]) for i in range(0, len(words), size)]


def extract_transcript(video_id):
    """Retrieve an English transcript using the stable legacy API first.

    The deployment previously relied on youtube-transcript-api's requests-based
    0.6.x interface. Pinning that interface avoids silently switching to the
    newer 1.x HTTP client when Streamlit Cloud rebuilds the app. The function
    still supports 1.x if the environment has it, but never repeats a request
    after an explicit YouTube IP/rate-limit block.
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    def snippets_to_text(snippets):
        parts = []
        for item in snippets:
            if hasattr(item, "text"):
                value = item.text
            elif isinstance(item, dict):
                value = item.get("text", "")
            else:
                value = ""
            if value:
                parts.append(str(value))
        return clean_transcript(" ".join(parts))

    def is_blocked(exc):
        message = f"{type(exc).__name__}: {exc}".lower()
        return any(marker in message for marker in (
            "requestblocked", "ip blocked", "blocking requests from your ip",
            "too many requests", "http 429", "status code 429",
            "forbidden", "http 403", "status code 403"
        ))

    errors = []

    # Preferred path: requests-based API used by the earlier working build.
    legacy_get = getattr(YouTubeTranscriptApi, "get_transcript", None)
    if callable(legacy_get):
        try:
            snippets = legacy_get(video_id, languages=["en", "en-US", "en-GB"])
            transcript = snippets_to_text(snippets)
            if transcript:
                return transcript
            errors.append("The English caption track was empty.")
        except Exception as exc:
            if is_blocked(exc):
                raise RuntimeError(
                    "YOUTUBE_REQUEST_BLOCKED: YouTube refused the caption request "
                    "from this server. The video ID is valid, but the hosting IP "
                    "is temporarily restricted. Please try again later or use the "
                    "transcript upload/paste option."
                ) from None
            errors.append(f"Legacy transcript API: {type(exc).__name__}: {exc}")

    # Compatibility path for youtube-transcript-api 1.x installations.
    try:
        api = YouTubeTranscriptApi()
        fetch = getattr(api, "fetch", None)
        if callable(fetch):
            snippets = fetch(video_id, languages=["en", "en-US", "en-GB"])
            transcript = snippets_to_text(snippets)
            if transcript:
                return transcript
            errors.append("The English caption track was empty.")
    except Exception as exc:
        if is_blocked(exc):
            raise RuntimeError(
                "YOUTUBE_REQUEST_BLOCKED: YouTube refused the caption request "
                "from this server. The video ID is valid, but the hosting IP "
                "is temporarily restricted. Please try again later or use the "
                "transcript upload/paste option."
            ) from None
        errors.append(f"Current transcript API: {type(exc).__name__}: {exc}")

    detail = " | ".join(errors[-2:])
    raise RuntimeError(
        "CAPTIONS_UNAVAILABLE: No English transcript could be retrieved. "
        "Check that captions are enabled for this video. " + detail
    )

def _concept_regex(concept):
    # Allow normal spaces or hyphens between words, with word boundaries.
    phrase = re.escape(concept.strip()).replace(r"\ ", r"[\s\-]+")
    return re.compile(rf"(?<![A-Za-z0-9]){phrase}(?![A-Za-z0-9])", re.IGNORECASE)


def _candidate_contexts(transcript, selected, max_words=65):
    """Find distinct transcript sentences/windows containing the chosen topic."""
    pattern = _concept_regex(selected)
    raw_sentences = re.split(r"(?<=[.!?])\s+|[\r\n]+", transcript)
    candidates, seen = [], set()

    def add_context(value):
        value = re.sub(r"\s+", " ", value).strip(" \t-,:;")
        words = value.split()
        if not (5 <= len(words) <= max_words) or not pattern.search(value):
            return
        norm = re.sub(r"\W+", " ", value.casefold()).strip()
        if norm not in seen:
            seen.add(norm)
            candidates.append(value)

    for sentence in raw_sentences:
        sentence = sentence.strip()
        if pattern.search(sentence):
            if len(sentence.split()) <= max_words:
                add_context(sentence)
            else:
                words = sentence.split()
                # Sliding windows; keep only windows containing the topic.
                for left in range(0, len(words), 18):
                    add_context(" ".join(words[left:left + max_words]))

    # Captions frequently omit punctuation. Search overlapping windows.
    if len(candidates) < QUIZ_QUESTION_COUNT:
        words = transcript.split()
        for left in range(0, max(1, len(words) - 6), 12):
            add_context(" ".join(words[left:left + max_words]))
            if len(candidates) >= 30:
                break
    return candidates


def _transcript_distractors(transcript, selected, detected, needed=3):
    """Collect distinct alternative terms that genuinely occur in the source."""
    selected_key = selected.casefold()
    pool, seen = [], {selected_key}

    def add(value):
        value = re.sub(r"\s+", " ", str(value)).strip(" .,:;!?-")
        key = value.casefold()
        if value and key not in seen and len(value) >= 3:
            seen.add(key)
            pool.append(value)

    for concept in detected:
        add(concept)

    # Add frequent source terms/phrases when the detected topic list is small.
    words = re.findall(r"[A-Za-z][A-Za-z0-9+#'-]{2,}", transcript)
    counts, display = {}, {}
    for word in words:
        key = word.strip("'-").lower()
        if key in _STOPWORDS or key == selected_key or len(key) < 3:
            continue
        counts[key] = counts.get(key, 0) + 1
        display.setdefault(key, word.strip("'-"))
    for key, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        add(display[key])
        if len(pool) >= needed:
            break
    return pool


def generate_questions(transcript, selected, count=QUIZ_QUESTION_COUNT):
    """Generate up to five transcript-grounded cloze MCQs for any subject.

    Every correct answer is a phrase present in the selected source context.
    Distractors are other terms extracted from the same transcript. This is a
    transparent template-based generator, not an LLM or semantic fact checker.
    """
    if not transcript or not selected or count < 1:
        return []

    contexts = _candidate_contexts(transcript, selected)
    if not contexts:
        return []

    detected = identify_concepts(transcript, limit=60)
    distractors = _transcript_distractors(transcript, selected, detected, needed=3)
    if len(distractors) < 3:
        return []

    seed_text = f"{selected.casefold()}|{transcript[:10000]}"
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:12], 16)
    rng = random.Random(seed)
    questions, used = [], set()
    pattern = _concept_regex(selected)

    for context in contexts:
        match = pattern.search(context)
        if not match:
            continue
        # Preserve source sentence while masking only the selected term.
        masked = context[:match.start()] + "________" + context[match.end():]
        norm = re.sub(r"\W+", " ", masked.casefold()).strip()
        if norm in used:
            continue
        used.add(norm)

        available = [x for x in distractors if x.casefold() != selected.casefold()]
        if len(available) < 3:
            break
        options = rng.sample(available, 3) + [selected]
        rng.shuffle(options)
        questions.append({
            "question": "According to the transcript, complete the statement:\n\n" + masked,
            "options": options,
            "answer": selected,
            "source_context": context,
        })
        if len(questions) >= min(count, QUIZ_QUESTION_COUNT):
            break
    return questions

def youtube_video_id(url):
    """Extract IDs from watch, youtu.be, shorts, embed, and live URLs."""
    from urllib.parse import urlparse, parse_qs
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
        host = (parsed.netloc or "").lower().replace("www.", "")
        if host in {"youtu.be", "m.youtube.com", "youtube.com", "music.youtube.com", "youtube-nocookie.com"}:
            if host == "youtu.be":
                candidate = parsed.path.strip("/").split("/")[0]
            else:
                query_id = parse_qs(parsed.query).get("v", [None])[0]
                path_parts = [part for part in parsed.path.split("/") if part]
                candidate = query_id
                if not candidate and len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live", "v"}:
                    candidate = path_parts[1]
            if candidate and re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                return candidate
    except Exception:
        return None
    return None


# -------------------- Session state --------------------
defaults = {
    "transcript": "", "concepts": [], "chunks": [], "questions": [],
    "score": None, "quiz_result": None, "quiz_number": 0, "video_url": "",
    "video_title": "", "session_started": None, "saved_attempt_ids": [],
}
for key, default in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = default

# -------------------- Shared presentation helpers --------------------
def page_header(kicker, title, subtitle):
    st.markdown(f'''<div class="page-head"><div class="kicker">{kicker}</div>
    <h1>{title}</h1><p>{subtitle}</p></div>''', unsafe_allow_html=True)

def panel_title(title, subtitle=None):
    st.markdown(f'<div class="panel-title">{title}</div>' + (f'<div class="panel-subtitle">{subtitle}</div>' if subtitle else ''), unsafe_allow_html=True)

def empty_state(title, message):
    st.markdown(f'''<div class="empty-state"><div class="empty-icon">✦</div>
    <div class="empty-title">{title}</div><div class="empty-copy">{message}</div></div>''', unsafe_allow_html=True)

st.markdown("""<style>
:root {
  --lt-bg: #f5f6f3;
  --lt-surface: #ffffff;
  --lt-ink: #202b2a;
  --lt-muted: #687572;
  --lt-accent: #3f6962;
  --lt-accent-dark: #2f514c;
  --lt-soft: #eaf0ed;
  --lt-line: #dfe6e1;
  --lt-gold: #a9824b;
}
html, body, [class*="css"] { font-family: Inter, "Segoe UI", Arial, sans-serif; }
.stApp { background: var(--lt-bg); color: var(--lt-ink); }
.block-container { max-width: 1180px; padding-top: 2.1rem; padding-bottom: 3rem; }
[data-testid="stHeader"] { background: rgba(245,246,243,.96); }
[data-testid="stSidebar"] { background: #edf1ee; border-right: 1px solid #d6dfd9; }
[data-testid="stSidebar"] *, [data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] [data-testid="stRadio"] label,
[data-testid="stSidebar"] [data-testid="stRadio"] label * { color: #263431 !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] label { padding: 11px 12px; border-radius: 8px; font-weight: 650; background: transparent; }
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover { background: #dfe8e2; }
[data-testid="stSidebar"] [data-testid="stRadio"] input:checked + div { color: #315e53 !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"] { border-color: #72847b !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"][aria-checked="true"] { background: #3f6962 !important; border-color: #3f6962 !important; }
[data-testid="stSidebar"] hr { border-color: #d1dbd4; }
[data-testid="stMetric"] { background: var(--lt-surface); border: 1px solid var(--lt-line); border-radius: 10px; padding: 16px 18px; box-shadow: none; }
[data-testid="stMetricLabel"] { color: var(--lt-muted); font-weight: 600; }
[data-testid="stMetricValue"] { color: var(--lt-ink); font-weight: 750; }
.stButton > button, .stFormSubmitButton > button { border: 1px solid var(--lt-accent); border-radius: 8px; padding: .58rem 1.05rem; font-weight: 650; color: #fff; background: var(--lt-accent); box-shadow: none; transition: background .15s ease, border-color .15s ease; }
.stButton > button:hover, .stFormSubmitButton > button:hover { color: #fff; background: var(--lt-accent-dark); border-color: var(--lt-accent-dark); }
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea, [data-baseweb="select"] > div { border: 1px solid #cbd5cf; border-radius: 7px; background: #fff; }
[data-testid="stTextInput"] label, [data-testid="stTextArea"] label, [data-testid="stSelectbox"] label, [data-testid="stRadio"] label, [data-testid="stCheckbox"] label, .stMarkdown p, .stMarkdown li, [data-testid="stCaptionContainer"] { color: var(--lt-ink) !important; }
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea, [data-baseweb="select"] *, [data-testid="stRadio"] *, [data-testid="stCheckbox"] * { color: #202b2a !important; }
[data-testid="stTextInput"] input::placeholder, [data-testid="stTextArea"] textarea::placeholder { color: #687572 !important; opacity: 1 !important; }
[data-testid="stSelectbox"] [data-baseweb="select"] > div { background: #fff !important; }
[data-testid="stAlert"] p, [data-testid="stForm"] label { color: #202b2a !important; }
[data-testid="stExpander"], [data-testid="stDataFrame"] { border: 1px solid var(--lt-line); border-radius: 9px; background: #fff; }
hr { border-color: var(--lt-line); }
h1, h2, h3 { color: var(--lt-ink); letter-spacing: -.025em; }
.page-head { margin: 0 0 24px; }
.kicker { color: var(--lt-accent); font-size: .7rem; font-weight: 750; letter-spacing: .14em; text-transform: uppercase; margin-bottom: 8px; }
.page-head h1 { font-size: 2rem; font-weight: 750; margin: 0 0 6px; }
.page-head p { color: var(--lt-muted); margin: 0; font-size: .96rem; }
.hero { background: #e9efeb; border: 1px solid #d9e3dd; border-left: 4px solid var(--lt-accent); padding: 27px 30px; border-radius: 10px; color: var(--lt-ink); box-shadow: none; margin-bottom: 24px; }
.hero .eyebrow { font-size: .69rem; letter-spacing: .14em; font-weight: 750; color: var(--lt-accent); text-transform: uppercase; }
.hero h1 { color: var(--lt-ink); font-size: 2.05rem; margin: 8px 0 7px; font-weight: 750; }
.hero p { color: #4e5e59; max-width: 760px; margin: 0; font-size: .98rem; line-height: 1.65; }
.section-label { font-size: 1.08rem; font-weight: 750; color: var(--lt-ink); margin: 24px 0 4px; }
.section-hint { font-size: .86rem; color: var(--lt-muted); margin-bottom: 13px; }
.panel { background: var(--lt-surface); border: 1px solid var(--lt-line); border-radius: 10px; padding: 18px 19px; margin: 10px 0; box-shadow: none; }
.panel-title { font-weight: 700; color: var(--lt-ink); font-size: 1rem; margin-bottom: 5px; }
.panel-subtitle { color: var(--lt-muted); font-size: .85rem; margin-bottom: 12px; }
.empty-state { background: #fff; border: 1px dashed #b9c8c0; border-radius: 10px; padding: 30px 20px; text-align: center; margin: 15px 0; }
.empty-icon { font-size: 1.5rem; color: var(--lt-accent); }
.empty-title { font-size: 1rem; font-weight: 700; color: var(--lt-ink); margin: 7px; }
.empty-copy { color: var(--lt-muted); font-size: .88rem; max-width: 560px; margin: auto; }
.concept-chip { display: inline-block; padding: 7px 11px; border-radius: 6px; background: var(--lt-soft); color: var(--lt-accent-dark); font-weight: 650; font-size: .8rem; margin: 4px 5px 4px 0; border: 1px solid #dce6df; }
.step-card { background: #fff; border: 1px solid var(--lt-line); border-radius: 10px; padding: 16px; height: 100%; }
.step-no { font-size: .7rem; font-weight: 750; color: var(--lt-gold); letter-spacing: .12em; }
.step-name { font-size: .96rem; font-weight: 700; color: var(--lt-ink); margin: 7px 0 4px; }
.step-copy { font-size: .83rem; color: var(--lt-muted); line-height: 1.55; }
@media (max-width: 700px) { .block-container { padding: 1rem; } .hero { padding: 22px; } .hero h1, .page-head h1 { font-size: 1.65rem; } }
/* Sidebar contrast safeguard: keep navigation text readable on the light sidebar. */
[data-testid="stSidebar"] [data-testid="stRadio"] label,
[data-testid="stSidebar"] [data-testid="stRadio"] label span,
[data-testid="stSidebar"] [data-testid="stRadio"] label p,
[data-testid="stSidebar"] [data-testid="stRadio"] label div { color: #263431 !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"] { outline-color: #72847b !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"][aria-checked="true"] { background: #3f6962 !important; border-color: #3f6962 !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"] svg { fill: #ffffff !important; color: #ffffff !important; }

/* High-contrast feedback and technical details on every theme. */
[data-testid="stAlert"] {
  background: #edf2ef !important;
  border: 1px solid #cbd8d0 !important;
  border-left: 4px solid #3f6962 !important;
  color: #202b2a !important;
}
[data-testid="stAlert"] *,
[data-testid="stAlert"] p,
[data-testid="stAlert"] span,
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] {
  color: #202b2a !important;
  opacity: 1 !important;
}
div[data-testid="stCode"] pre,
div[data-testid="stCode"] code,
.stCode pre,
.stCode code {
  color: #f5f6f3 !important;
  background: #263431 !important;
  opacity: 1 !important;
}
/* Explicitly readable status panels: white text on dark status colours. */
[data-testid="stAlert"] {
  background: #3b514b !important;
  border: 1px solid #536c64 !important;
  border-left: 4px solid #b79a6a !important;
  color: #ffffff !important;
}
[data-testid="stAlert"] *,
[data-testid="stAlert"] p,
[data-testid="stAlert"] span,
[data-testid="stAlert"] div,
[data-testid="stAlert"] [data-testid="stMarkdownContainer"],
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] * {
  color: #ffffff !important;
  opacity: 1 !important;
  -webkit-text-fill-color: #ffffff !important;
}
/* Expander header (e.g. Review your answers) must contrast with its dark bar. */
[data-testid="stExpander"] details > summary,
[data-testid="stExpander"] details > summary *,
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary span,
[data-testid="stExpander"] summary p,
[data-testid="stExpander"] summary svg {
  background-color: #263431 !important;
  color: #ffffff !important;
  fill: #ffffff !important;
  opacity: 1 !important;
  -webkit-text-fill-color: #ffffff !important;
}
[data-testid="stExpander"] details > summary { border-radius: 8px 8px 0 0 !important; }
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
  background: #ffffff !important;
  color: #202b2a !important;
}
[data-testid="stExpander"] [data-testid="stExpanderDetails"] * {
  opacity: 1 !important;
}
/* Keep error feedback legible even when Streamlit applies its own nested styles. */
[data-testid="stAlert"] svg { color: #ffffff !important; fill: #ffffff !important; }
/* File uploader: visible labels, button and drop-zone on the sage page. */
[data-testid="stFileUploader"] section,
[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] {
  background: #ffffff !important;
  border: 1px dashed #9aafa4 !important;
  border-radius: 9px !important;
}
[data-testid="stFileUploader"] *,
[data-testid="stFileUploader"] small,
[data-testid="stFileUploader"] span,
[data-testid="stFileUploader"] button {
  color: #263431 !important;
  opacity: 1 !important;
}
[data-testid="stFileUploader"] button {
  background: #eaf0ed !important;
  border: 1px solid #cbd8d0 !important;
}
/* Technical output remains readable without a near-black-on-green clash. */
[data-testid="stCode"] pre, [data-testid="stCode"] code {
  background: #263431 !important;
  color: #ffffff !important;
  -webkit-text-fill-color: #ffffff !important;
}
</style>""", unsafe_allow_html=True)

st.sidebar.markdown('''<div style="padding:18px 4px 20px"><div style="font-size:2.1rem">🎓</div>
<div style="font-size:1.35rem;font-weight:800;letter-spacing:-.04em">LearnTube AI</div>
<div style="font-size:.78rem;color:#52645c">ADAPTIVE VIDEO LEARNING</div></div>''', unsafe_allow_html=True)
st.sidebar.markdown('---')
st.sidebar.markdown('<div style="font-size:.72rem;color:#52645c;letter-spacing:.13em;font-weight:800;padding:4px 10px 9px">WORKSPACE</div>', unsafe_allow_html=True)
page = st.sidebar.radio("Navigation", ["Dashboard", "Video Learning", "Quiz", "Analytics", "About"], label_visibility="collapsed")
st.sidebar.markdown('---')
st.sidebar.markdown('<div style="padding:8px 10px;color:#52645c;font-size:.78rem;line-height:1.6">Learn smarter.<br>One video at a time.</div>', unsafe_allow_html=True)

# -------------------- Dashboard --------------------
if page == "Dashboard":
    st.markdown('''<div class="hero"><div class="eyebrow">YOUR PERSONAL LEARNING SPACE</div>
    <h1>LearnTube AI</h1><p>Turn educational videos into structured learning. Discover key concepts, practise with transcript-based quizzes, and follow your learning progress in one place.</p></div>''', unsafe_allow_html=True)
    st.markdown('<div class="section-label">Your learning at a glance</div><div class="section-hint">A quick summary of your current learning session.</div>', unsafe_allow_html=True)
    a,b,c,d=st.columns(4)
    a.metric("Concepts found",len(st.session_state.concepts),help="Supported concepts identified in the current transcript")
    b.metric("Transcript chunks",len(st.session_state.chunks))
    c.metric("Latest quiz score",f"{st.session_state.score}%" if st.session_state.score is not None else "—")
    d.metric("Learning status","In progress" if st.session_state.transcript else "Not started")
    st.markdown('<div class="section-label">Get started</div><div class="section-hint">Follow these steps to turn a video into an active learning session.</div>',unsafe_allow_html=True)
    x,y,z=st.columns(3)
    for col,no,title,copy in [(x,"01","Add a video","Paste a YouTube link or provide its transcript."),(y,"02","Explore concepts","Review recurring topic phrases extracted from the transcript."),(z,"03","Take a quiz","Answer topic-based questions and review your score.")]:
        with col: st.markdown(f'<div class="step-card"><div class="step-no">STEP {no}</div><div class="step-name">{title}</div><div class="step-copy">{copy}</div></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-label">Continue learning</div>',unsafe_allow_html=True)
    left,right=st.columns([1.4,1])
    with left:
        if st.session_state.transcript:
            st.markdown(f'<div class="panel"><div class="panel-title">{st.session_state.video_title or "Current learning session"}</div><div class="panel-subtitle">{len(st.session_state.transcript.split())} words · {len(st.session_state.chunks)} chunks</div><span class="concept-chip">Transcript processed</span></div>',unsafe_allow_html=True)
        else: empty_state("No active video yet","Open Video Learning to add a YouTube video or paste a transcript.")
    with right:
        st.markdown('<div class="panel"><div class="panel-title">Learning tip</div><div class="panel-subtitle">For better quiz results, use a transcript with clear explanations and enough context for each concept.</div></div>',unsafe_allow_html=True)


# -------------------- Video learning --------------------
elif page == "Video Learning":
    page_header("LEARNING WORKSPACE", "Video Learning", "Bring an educational video into your learning workspace and explore its transcript.")
    left, right = st.columns([1.25, .75], gap="large")

    with left:
        st.markdown('<div class="panel-title">01 · Add your learning material</div><div class="panel-subtitle">Use a YouTube URL with available captions, or paste transcript text directly.</div>', unsafe_allow_html=True)
        url = st.text_input("YouTube video URL", value=st.session_state.video_url, placeholder="https://www.youtube.com/watch?v=...")
        st.markdown('<div style="text-align:center;color:#687572;font-size:.8rem;padding:5px">— OR PASTE TRANSCRIPT —</div>', unsafe_allow_html=True)
        transcript_file = st.file_uploader(
            "Or upload a transcript file (.txt, .srt, .vtt)",
            type=["txt", "srt", "vtt"],
            help="Upload a TXT, SRT or VTT transcript. If transcript text or a file is provided, it is used instead of attempting YouTube caption retrieval.",
            key="transcript_upload",
        )
        transcript_input = st.text_area("Or paste transcript text", height=190, placeholder="Paste the video transcript here...")
        process = st.button("Process learning material", use_container_width=True)

    with right:
        st.markdown('''<div class="panel">
        <div class="panel-title">How it works</div>
        <div class="panel-subtitle">Your transcript is processed in three stages.</div>
        <div class="step-no">01</div><b>Clean transcript</b>
        <p style="color:#687572;font-size:.85rem">Normalize the text for analysis.</p>
        <div class="step-no">02</div><b>Split into chunks</b>
        <p style="color:#687572;font-size:.85rem">Divide transcript into manageable sections.</p>
        <div class="step-no">03</div><b>Identify concepts</b>
        <p style="color:#687572;font-size:.85rem">Find supported learning topics.</p></div>''', unsafe_allow_html=True)
        st.markdown('''<div class="panel"><div class="panel-title">Supported input</div>
        <div class="panel-subtitle">YouTube captions, pasted text, or uploaded TXT/SRT/VTT files.</div>
        <span class="concept-chip">YouTube captions</span>
        <span class="concept-chip">TXT / SRT / VTT</span>
        <span class="concept-chip">Concept analysis</span></div>''', unsafe_allow_html=True)

    if process:
        text = transcript_input.strip()
        source_is_youtube = False
        source_is_file = False

        if not text and transcript_file is not None:
            try:
                text = transcript_file.getvalue().decode("utf-8-sig", errors="replace")
                source_is_file = True
            except Exception as exc:
                st.error(f"Could not read the uploaded transcript file: {exc}")

        if not text and not source_is_file and url.strip():
            video_id = youtube_video_id(url)
            if not video_id:
                st.error("This does not look like a valid YouTube video URL. Use a watch, youtu.be, shorts, embed, or live link.")
            else:
                source_is_youtube = True
                try:
                    with st.spinner("Checking available YouTube captions..."):
                        text = extract_transcript(video_id)
                except Exception as exc:
                    error_text = str(exc)
                    if "YOUTUBE_REQUEST_BLOCKED" in error_text or "YOUTUBE_CLOUD_BLOCK" in error_text or "RequestBlocked" in error_text or "blocking requests from your IP" in error_text:
                        st.error("YouTube blocked the caption request from this server. The URL was accepted, but the hosting IP was refused. This is not a transcript-language or URL-format error.")
                    else:
                        st.warning("Automatic captions could not be retrieved. Captions may be disabled or restricted for this video.")
                    st.markdown("**Use the reliable transcript input below:** open the video on YouTube → select **Show transcript** → copy the transcript → paste it into the transcript box, or save it as a `.txt`, `.srt`, or `.vtt` file and upload it.")
                    st.caption("The app will process pasted or uploaded transcript text without making another YouTube request.")
        elif not text and not url.strip():
            st.warning("Enter a YouTube URL or paste transcript text to continue.")

        if text:
            cleaned = clean_transcript(text)
            if len(cleaned.split()) < 8:
                st.warning("The transcript is too short to create meaningful questions. Please provide at least a few complete sentences.")
            else:
                st.session_state.transcript = cleaned
                st.session_state.chunks = split_transcript(cleaned)
                st.session_state.concepts = identify_concepts(cleaned)
                st.session_state.video_url = url.strip() if source_is_youtube else ""
                video_id = youtube_video_id(url) if source_is_youtube else None
                if video_id:
                    st.session_state.video_title = f"YouTube video {video_id}"
                elif source_is_file and transcript_file is not None:
                    st.session_state.video_title = transcript_file.name
                else:
                    st.session_state.video_title = "Pasted transcript"
                st.session_state.session_started = datetime.now(timezone.utc).isoformat()
                st.session_state.questions = []
                st.session_state.quiz_result = None
                st.session_state.quiz_topic = None
                st.session_state.score = None
                st.success("Transcript processed successfully. Review the concepts below.")

    if st.session_state.transcript:
        st.markdown('<div class="section-label">Transcript analysis</div><div class="section-hint">Review the processing summary and concepts found.</div>', unsafe_allow_html=True)
        m1, m2, m3 = st.columns(3)
        m1.metric("Word count", f"{len(st.session_state.transcript.split()):,}")
        m2.metric("Chunks created", len(st.session_state.chunks))
        m3.metric("Concepts identified", len(st.session_state.concepts))

        st.markdown('<div class="panel-title">Identified concepts</div>', unsafe_allow_html=True)
        if st.session_state.concepts:
            st.markdown(''.join(f'<span class="concept-chip">{html.escape(c)}</span>' for c in st.session_state.concepts), unsafe_allow_html=True)
        else:
            st.warning("No clear topic terms were detected. Try uploading a cleaner transcript or one with more explanatory content. You can still process a transcript from any subject; the detector is based on recurring words and phrases, not a fixed subject list.")
        with st.expander("Read processed transcript"):
            st.write(st.session_state.transcript)

        if st.session_state.concepts:
            st.markdown('<div class="section-label">Create your quiz</div><div class="section-hint">Choose a detected concept to prepare up to five transcript-based multiple-choice questions.</div>', unsafe_allow_html=True)
            current_topic = st.session_state.get("quiz_topic")
            topic_index = st.session_state.concepts.index(current_topic) if current_topic in st.session_state.concepts else 0
            chosen_topic = st.selectbox("Choose a concept for your quiz", st.session_state.concepts, index=topic_index, key="video_quiz_topic")
            if st.button("Generate quiz from this transcript", use_container_width=True, key="make_quiz_video"):
                made_questions = generate_questions(st.session_state.transcript, chosen_topic, count=QUIZ_QUESTION_COUNT)
                st.session_state.questions = made_questions
                st.session_state.quiz_topic = chosen_topic if made_questions else None
                st.session_state.quiz_result = None
                st.session_state.score = None
                st.session_state.quiz_number += 1
                if made_questions:
                    st.success(f"Prepared {len(made_questions)} question(s). Open the Quiz page to answer them.")
                    if len(made_questions) < QUIZ_QUESTION_COUNT:
                        st.warning("Fewer than five distinct transcript contexts were available, so the quiz contains only the questions that could be supported.")
                else:
                    st.warning("No suitable sentence was found for this concept. Choose another concept or paste a longer transcript with explanatory sentences.")
            st.caption("Question method: transcript-based sentence completion. The correct term is taken from the selected transcript context; alternative terms are drawn from the same transcript. This is template-based question generation, not an external AI/LLM or a semantic fact-checker.")
    else:
        empty_state("Your workspace is ready", "Add a YouTube video or paste transcript text to begin.")


# -------------------- Quiz --------------------
elif page == "Quiz":
    page_header("CHECK YOUR UNDERSTANDING", "Knowledge Check", "Practise with questions based on concepts found in your processed transcript.")

    if not st.session_state.transcript:
        empty_state("No transcript available", "Go to Video Learning and process a transcript before generating a quiz.")
    elif not st.session_state.concepts:
        empty_state("No supported concepts found", "The detector looks for repeated topic words and phrases across many subject areas. Try a longer transcript with clear explanations, or paste a cleaner transcript.")
    else:
        qcol, overview = st.columns([1.35, 1])
        with qcol:
            st.markdown('<div class="panel-title">Choose your quiz topic</div><div class="panel-subtitle">Select a concept identified in your transcript.</div>', unsafe_allow_html=True)
            current_topic = st.session_state.get("quiz_topic")
            topic_index = st.session_state.concepts.index(current_topic) if current_topic in st.session_state.concepts else 0
            selected = st.selectbox("Learning topic", st.session_state.concepts, index=topic_index, key="quiz_topic_select")
            if st.button("Generate quiz questions", use_container_width=True, key="generate_quiz_page"):
                questions = generate_questions(st.session_state.transcript, selected, count=QUIZ_QUESTION_COUNT)
                st.session_state.questions = questions
                st.session_state.quiz_topic = selected if questions else None
                st.session_state.quiz_result = None
                st.session_state.score = None
                st.session_state.quiz_number += 1
                if questions:
                    st.success(f"Your quiz contains {len(questions)} transcript-based question(s).")
                    if len(questions) < QUIZ_QUESTION_COUNT:
                        st.info("The transcript contained fewer than five distinct suitable contexts. Only supported questions were included.")
                else:
                    st.warning("No suitable question contexts were found for this topic. Try a different concept or a longer transcript.")
        with overview:
            st.markdown(f'''<div class="panel"><div class="panel-title">Quiz overview</div>
            <div class="panel-subtitle">Current learning material</div>
            <div style="font-size:1.3rem;font-weight:800;color:#202b2a">{len(st.session_state.concepts)} topics</div>
            <p style="color:#687572;font-size:.85rem">Up to five questions are prepared from distinct transcript contexts.</p></div>''', unsafe_allow_html=True)

        questions = st.session_state.questions
        if questions and not st.session_state.quiz_result:
            st.markdown('<div class="section-label">Your quiz</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="panel-title">{st.session_state.quiz_topic}</div><div class="panel-subtitle">{len(questions)} question(s) · Select one answer for each question.</div>', unsafe_allow_html=True)
            with st.form(f"quiz_form_{st.session_state.quiz_number}"):
                answers = []
                for i, question in enumerate(questions):
                    st.markdown(f'<div class="panel-title" style="margin-top:12px">QUESTION {i + 1:02d} <span style="color:#687572;font-weight:500">/ {len(questions):02d}</span></div>', unsafe_allow_html=True)
                    question_html = html.escape(question["question"]).replace(chr(10), "<br>")
                    st.markdown(f'<div class="panel" style="font-size:1rem;font-weight:600;color:#202b2a;line-height:1.65">{question_html}</div>', unsafe_allow_html=True)
                    answer = st.radio("Choose your answer", question["options"], index=None, key=f"quiz_{st.session_state.quiz_number}_{i}", label_visibility="collapsed")
                    answers.append(answer)
                    st.divider()
                submitted = st.form_submit_button("Submit answers", use_container_width=True)

            if submitted:
                if any(answer is None for answer in answers):
                    st.warning(f"Please answer all {len(questions)} questions before submitting.")
                else:
                    correct = sum(answers[i] == questions[i]["answer"] for i in range(len(questions)))
                    percentage = round(correct / len(questions) * 100)
                    attempt_id = str(uuid.uuid4())
                    completed_at = datetime.now(timezone.utc).isoformat()
                    result = {"answers": answers, "correct": correct, "total": len(questions), "percentage": percentage, "attempt_id": attempt_id}
                    st.session_state.quiz_result = result
                    st.session_state.score = percentage

                    attempt = {
                        "attempt_id": attempt_id,
                        "completed_at_utc": completed_at,
                        "video_title": st.session_state.video_title,
                        "video_url": st.session_state.video_url,
                        "topic": st.session_state.quiz_topic,
                        "score": correct,
                        "total": len(questions),
                        "percentage": percentage,
                        "questions": [
                            {"question": questions[i]["question"], "options": questions[i]["options"],
                             "correct_answer": questions[i]["answer"], "user_answer": answers[i],
                             "is_correct": answers[i] == questions[i]["answer"]}
                            for i in range(len(questions))
                        ]
                    }
                    try:
                        with st.spinner("Saving your quiz attempt to GitHub..."):
                            save_attempt(attempt)
                        st.session_state.saved_attempt_ids.append(attempt_id)
                        st.success("Quiz result saved to GitHub analytics.")
                    except Exception as exc:
                        st.error("Your quiz was scored, but saving to GitHub failed. The result remains visible in this session.")
                        with st.expander("Storage technical details"):
                            st.code(str(exc)[:1200], language=None)

        # Show result separately and hide the submitted form to prevent duplicate attempts.
        if st.session_state.quiz_result and st.session_state.questions:
            result = st.session_state.quiz_result
            st.markdown('<div class="section-label">Your result</div>', unsafe_allow_html=True)
            r1, r2, r3 = st.columns(3)
            r1.metric("Correct answers", f"{result['correct']} / {result['total']}")
            r2.metric("Final score", f"{result['percentage']}%")
            r3.metric("Performance", "High" if result["percentage"] >= 80 else "Medium" if result["percentage"] >= 50 else "Needs revision")
            if result["percentage"] >= 80:
                st.success("High performance: try a more advanced learning topic.")
            elif result["percentage"] >= 50:
                st.info("Medium performance: revise this concept and practise again.")
            else:
                st.warning("Keep practising: revisit the transcript and review the concept.")
            with st.expander("Review your answers", expanded=True):
                for i, question in enumerate(st.session_state.questions):
                    st.markdown(f"**Question {i + 1}:** {question['question']}")
                    st.write(f"Your answer: {result['answers'][i]}")
                    st.write(f"Correct answer: {question['answer']}")
                    if result["answers"][i] == question["answer"]:
                        st.success("Correct")
                    else:
                        st.error("Incorrect")
            if st.button("Prepare another quiz", key="prepare_another_quiz"):
                st.session_state.questions = []
                st.session_state.quiz_result = None
                st.session_state.quiz_topic = None
                st.rerun()
        elif not st.session_state.questions:
            empty_state("No quiz prepared yet", "Choose a learning topic above and generate questions from the processed transcript.")


# -------------------- Analytics --------------------
elif page == "Analytics":
    page_header("LEARNING INSIGHTS","Learning Analytics","Track quiz attempts, review scores, and understand topic-wise performance.")
    try:
        records,_=load_history()
        if not records: empty_state("No analytics to display yet","Complete and save your first quiz. Your learning history will appear here.")
        else:
            history=pd.DataFrame(records)
            if "completed_at_utc" in history.columns:
                history["completed_at_utc"]=pd.to_datetime(history["completed_at_utc"],errors="coerce",utc=True)
                history=history.sort_values("completed_at_utc",ascending=False)
            c1,c2,c3,c4=st.columns(4)
            c1.metric("Quiz attempts",len(history));c2.metric("Average score",f"{history['percentage'].mean():.1f}%");c3.metric("Best score",f"{history['percentage'].max():.0f}%");c4.metric("Questions answered",int(history["total"].sum()))
            st.markdown('<div class="section-label">Performance over time</div><div class="section-hint">Your saved quiz scores in chronological order.</div>',unsafe_allow_html=True)
            chart=history.copy()
            if "completed_at_utc" in chart.columns: chart=chart.sort_values("completed_at_utc").set_index("completed_at_utc")
            st.line_chart(chart["percentage"],use_container_width=True)
            if "topic" in history.columns:
                st.markdown('<div class="section-label">Topic-wise performance</div>',unsafe_allow_html=True)
                topic_avg=history.groupby("topic")["percentage"].mean().sort_values(ascending=False)
                st.bar_chart(topic_avg,use_container_width=True)
            st.markdown('<div class="section-label">Recent quiz history</div>',unsafe_allow_html=True)
            cols=[c for c in ["completed_at_utc","video_title","topic","score","total","percentage"] if c in history.columns]
            st.dataframe(history[cols],use_container_width=True,hide_index=True)
            with st.expander("View complete saved attempt records"): st.json(records)
    except Exception as exc:
        st.error(f"Unable to load permanent analytics from GitHub: {exc}")
        st.info("Check that GITHUB_TOKEN is configured in Streamlit Secrets and has Contents read/write permission for this repository.")

# -------------------- About --------------------
elif page == "About":
    page_header("ABOUT THE PROJECT","Learning should be active, not passive.","LearnTube AI supports structured learning from educational video transcripts.")
    st.markdown('<div class="hero"><div class="eyebrow">PROJECT OVERVIEW</div><h1 style="font-size:1.8rem">LearnTube AI</h1><p>An adaptive educational video learning platform that processes transcripts, identifies supported concepts, prepares concept-based quizzes, evaluates performance, and records learning progress.</p></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-label">How the platform works</div>',unsafe_allow_html=True)
    a,b,c=st.columns(3)
    for col,n,t,d in [(a,"01","Transcript processing","Add a YouTube video or paste its transcript. The text is cleaned and split into chunks."),(b,"02","Concept identification","The application extracts recurring topic phrases from the transcript."),(c,"03","Quiz and analytics","Generate topic-based questions, review your result, and track saved attempts.")]:
        with col: st.markdown(f'<div class="step-card"><div class="step-no">STAGE {n}</div><div class="step-name">{t}</div><div class="step-copy">{d}</div></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-label">Core features</div>',unsafe_allow_html=True)
    f1,f2=st.columns(2)
    with f1: st.markdown('<div class="panel"><div class="panel-title">Video-based learning</div><div class="panel-subtitle">Transcript input, cleaning, chunking, and supported concept identification.</div></div><div class="panel"><div class="panel-title">Concept-based quizzes</div><div class="panel-subtitle">Up to five transcript-context questions for a selected concept.</div></div>',unsafe_allow_html=True)
    with f2: st.markdown('<div class="panel"><div class="panel-title">Performance feedback</div><div class="panel-subtitle">Score calculation, answer review, and performance-level feedback.</div></div><div class="panel"><div class="panel-title">Learning analytics</div><div class="panel-subtitle">Quiz history and score analytics stored using GitHub-backed JSON.</div></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-label">Technology stack</div>',unsafe_allow_html=True)
    st.markdown('<span class="concept-chip">Python</span><span class="concept-chip">Streamlit</span><span class="concept-chip">Pandas</span><span class="concept-chip">YouTube Transcript API</span><span class="concept-chip">Requests</span><span class="concept-chip">GitHub API</span>',unsafe_allow_html=True)
    st.caption("Developed as an academic Machine Learning project.")
