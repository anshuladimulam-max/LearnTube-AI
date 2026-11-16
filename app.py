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

st.set_page_config(page_title="LearnTube AI", page_icon=None, layout="wide")

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


# -------------------- Persistent generated quiz sets --------------------
def quiz_sets_file():
    return str(get_setting("QUIZ_SETS_FILE", "data/quiz_sets.json"))


def load_quiz_sets():
    token, repo, branch, _ = github_config()
    path = quiz_sets_file()
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    response = requests.get(url, headers=github_headers(token), params={"ref": branch}, timeout=20)
    if response.status_code == 404:
        return [], None
    if response.status_code != 200:
        raise RuntimeError(f"GitHub could not load saved quiz sets (HTTP {response.status_code}): {response.text[:300]}")
    import base64
    try:
        data = json.loads(base64.b64decode(response.json()["content"]).decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("The saved quiz sets file is not valid JSON.") from exc
    if not isinstance(data, list):
        raise RuntimeError("The saved quiz sets file must contain a JSON list.")
    return data, response.json().get("sha")


def save_quiz_set(quiz_set):
    token, repo, branch, _ = github_config()
    latest, sha = load_quiz_sets()
    if any(item.get("set_id") == quiz_set["set_id"] for item in latest):
        return latest
    latest.append(quiz_set)
    import base64
    content = json.dumps(latest, ensure_ascii=False, indent=2)
    body = {
        "message": f"Save LearnTube question set {quiz_set['set_id']}",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    url = f"{GITHUB_API}/repos/{repo}/contents/{quiz_sets_file()}"
    response = requests.put(url, headers=github_headers(token), json=body, timeout=25)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"GitHub could not save the question set (HTTP {response.status_code}): {response.text[:350]}")
    return latest


# -------------------- Learning and quiz helpers --------------------
# Questions are prepared from the complete transcript, without category labels.
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

def _phrase_regex(concept):
    # Allow normal spaces or hyphens between words, with word boundaries.
    phrase = re.escape(concept.strip()).replace(r"\ ", r"[\s\-]+")
    return re.compile(rf"(?<![A-Za-z0-9]){phrase}(?![A-Za-z0-9])", re.IGNORECASE)


def _extract_overall_quiz_terms(transcript):
    """Extract useful source terms for an overall transcript quiz.

    This is only an internal answer/distractor helper. The interface does not
    classify the video into topics or ask the learner to choose a category.
    Terms containing conversational fragments, function words, or common
    narration verbs are rejected.
    """
    if not transcript:
        return []

    # Only "of" is allowed inside extracted technical phrases. Other
    # conjunctions/prepositions tend to create fragments from spoken captions.
    connectors = {"of"}
    noisy = set(_STOPWORDS) | set(_VERB_NOISE) | {
        "im", "ive", "ill", "youll", "youre", "theyre", "were", "thats",
        "theres", "heres", "okay", "yeah", "right", "actually", "basically",
        "literally", "gonna", "wanna", "dont", "doesnt", "didnt", "isnt",
        "wasnt", "wouldnt", "couldnt", "shouldnt", "cant", "cannot", "lets",
        "thing", "things", "stuff", "kind", "sort", "lot", "lots", "bit",
        "something", "anything", "everything", "someone", "anyone", "everyone",
        "head", "guys", "look", "okay", "yeah", "right", "actually",
        # Common spoken/transcription verbs and incomplete phrase endings.
        "convert", "converts", "combines", "combine", "minimizes", "minimize",
        "generalizes", "generalize", "evaluates", "evaluate", "estimates", "estimate",
        "describes", "describe", "explains", "explain", "shows", "show", "uses",
        "using", "used", "makes", "make", "creates", "create", "provides",
        "provide", "requires", "require", "increases", "increase", "decreases",
        "decrease", "depends", "depend", "contains", "contain", "includes",
        "include", "absorbs", "absorb", "produces", "produce", "stores", "store",
        "powered", "power", "measures", "measure", "updates", "update", "improves",
        "improve", "begins", "began", "occurs", "occur", "remains", "remain",
        "unseen", "different", "actual", "predicted", "independent", "dependent",
        "continuous", "important", "general", "various", "several", "specific",
        "equals", "multiplied", "divided", "expanded", "involved", "resulting",
        "releases", "release", "released", "powered", "power", "increased",
        "increases", "moved", "move", "changed", "changes", "changing",
        "started", "starts", "begins", "began", "developed", "develops",
        "consists", "represents", "represented", "calculated", "calculates",
        "determines", "determine", "determined", "indicates", "indicate", "occurred",
        "happens", "happen", "formed", "forms", "form", "called", "known"
    }
    # Filter common verbs and discourse connectors that otherwise create
    # distractors such as "Classification predicts" or "model learns".
    noisy.update({
        "predict", "predicts", "predicted", "predicting", "learns", "learn",
        "split", "splits", "contain", "contains", "estimate", "estimates",
        "estimated", "update", "updates", "updated", "penalize", "penalizes",
        "reduce", "reduces", "reduced", "perform", "performs", "performed",
        "select", "selects", "selected", "lead", "leads", "create", "creates",
        "created", "measure", "measures", "measured", "fit", "fits", "fitting",
        "represent", "represents", "refer", "refers", "referring", "based",
        "rather", "than", "allow", "allows", "allowing", "apply", "applies",
        "applied", "calculate", "calculates", "calculated", "compare", "compares",
        "compared", "identify", "identifies", "identified", "classify", "classifies",
        "classifies", "group", "groups", "grouped", "include", "includes",
        "included", "consist", "consists", "consisting", "result", "results",
        "resulting", "depend", "depends", "depending", "occur", "occurs",
        "occurred", "happen", "happens", "happened", "provide", "provides",
        "provided", "explain", "explains", "explained", "describe", "describes",
        "described", "discuss", "discusses", "discussed", "show", "shows", "shown",
        "tell", "tells", "told", "call", "calls", "called", "know", "knows",
        "known", "make", "makes", "made", "become", "becomes", "became",
        "give", "gives", "given", "get", "gets", "got", "take", "takes", "taken",
    })
    # These words may be ordinary language elsewhere, but are meaningful
    # technical vocabulary in educational transcripts and should be retained.
    noisy.difference_update({"learning", "training", "classification", "regression",
                             "clustering", "model", "models", "data", "algorithm",
                             "algorithms", "network", "networks", "feature", "features",
                             "variable", "variables", "energy", "force", "mass",
                             "momentum", "velocity", "acceleration", "photosynthesis",
                             "chlorophyll", "glucose", "oxygen", "carbon", "dioxide"})
    counts = {}
    display = {}
    # Keep the original sentence order for transcript-wide coverage.
    raw_sentences = [re.sub(r"\s+", " ", part).strip()
                     for part in re.split(r"(?<=[.!?])\s+|[\r\n]+", transcript)
                     if part.strip()]
    if len(raw_sentences) <= 1:
        words = transcript.split()
        raw_sentences = [" ".join(words[i:i + 32])
                         for i in range(0, max(1, len(words) - 7), 20)]

    for sentence in raw_sentences:
        # Commas and semicolons often separate spoken lists; do not join words
        # across them into artificial phrases such as "mass gravity".
        for segment in re.split(r"[,;:]", sentence):
            tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#'-]*", segment)
            normalized = [re.sub(r"[^a-z0-9+#]", "", token.casefold()) for token in tokens]
            originals = [token.strip("'-") for token in tokens]
            for size in (2, 3, 4):
                for i in range(len(normalized) - size + 1):
                    gram = normalized[i:i + size]
                    shown = originals[i:i + size]
                    if any(not token for token in gram):
                        continue
                    if gram[0] in noisy or gram[-1] in noisy:
                        continue
                    if any(token in noisy and token not in connectors for token in gram):
                        continue
                    content = [token for token in gram if token not in connectors]
                    if len(content) < 2 or any(token in noisy or len(token) < 3 for token in content):
                        continue
                    key = " ".join(gram)
                    counts[key] = counts.get(key, 0) + 1
                    display.setdefault(key, " ".join(shown))

    ranked = []
    for key, count in counts.items():
        size = len(key.split())
        # Single words must recur; multiword technical phrases can be useful
        # even when mentioned once in a short lecture.
        if size < 2:
            continue
        score = count * (1 + 0.35 * (size - 1))
        ranked.append((score, count, size, key, display[key]))
    ranked.sort(key=lambda item: (-item[0], -item[2], -item[1], item[3]))

    # Remove shorter phrases that are wholly contained in a longer candidate.
    # This prevents distractors such as "best fit" beside "best fit line".
    candidate_keys = [item[3] for item in ranked]
    pruned = []
    for score, count, size, key, label in ranked:
        key_tokens = key.split()
        is_fragment = False
        for other in candidate_keys:
            other_tokens = other.split()
            if len(other_tokens) <= len(key_tokens):
                continue
            if any(other_tokens[i:i + len(key_tokens)] == key_tokens
                   for i in range(len(other_tokens) - len(key_tokens) + 1)):
                is_fragment = True
                break
        if not is_fragment:
            pruned.append((score, count, size, key, label))

    results, seen = [], set()
    generic_phrase_endings = {
        "performance", "iterations", "value", "examples", "patterns",
        "predictions", "line", "output", "process", "settings", "data",
        "results", "information", "things", "way", "ways"
    }
    for _, _, _, key, label in pruned:
        if key in seen:
            continue
        # Avoid turning ordinary descriptive fragments into answer choices.
        if key.split()[-1] in generic_phrase_endings:
            continue
        results.append({"key": key, "label": label, "size": len(key.split())})
        seen.add(key)
        if len(results) >= 100:
            break
    return results


def generate_overall_questions(transcript, count=QUIZ_QUESTION_COUNT, previous_questions=None):
    """Create varied concept-understanding MCQs from across the full transcript.

    The learner is not asked to complete a sentence or fill a blank. Each item
    presents a short explanation from the transcript and asks which concept,
    method, or idea it describes. Contexts are selected across the beginning,
    middle, and end of the transcript. No category selection is shown.
    """
    if not transcript or count < 1:
        return []

    terms = _extract_overall_quiz_terms(transcript)
    if len(terms) < 4:
        return []

    previous_questions = previous_questions or []
    used_questions = {
        re.sub(r"\W+", " ", str(item.get("question", "")).casefold()).strip()
        for item in previous_questions if item.get("question")
    }

    sentences = [
        re.sub(r"\s+", " ", part).strip()
        for part in re.split(r"(?<=[.!?])\s+|[\r\n]+", transcript)
        if part.strip()
    ]
    # Captions often contain very long run-on lines. Split these into overlapping
    # windows so question candidates are not limited to the start of the video.
    expanded = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) > 55:
            for i in range(0, len(words), 32):
                chunk = " ".join(words[i:i + 48]).strip()
                if 9 <= len(chunk.split()) <= 55:
                    expanded.append(chunk)
        elif 8 <= len(words) <= 55:
            expanded.append(sentence)
    if len(expanded) < 2:
        words = transcript.split()
        expanded = [" ".join(words[i:i + 48]) for i in range(0, max(1, len(words) - 8), 32)]

    term_patterns = [(item, _phrase_regex(item["label"])) for item in terms]
    candidates = []
    seen_contexts = set()
    for index, sentence in enumerate(expanded):
        normalized_context = re.sub(r"\W+", " ", sentence.casefold()).strip()
        if normalized_context in seen_contexts:
            continue
        seen_contexts.add(normalized_context)
        matches = []
        for item, pattern in term_patterns:
            match = pattern.search(sentence)
            if match:
                matches.append((item, match))
        # A sentence with several extracted phrases can generate multiple
        # possible questions, but each candidate is checked against prior sets.
        matches.sort(key=lambda entry: (-entry[0]["size"], entry[0]["key"]))
        for item, match in matches[:3]:
            candidates.append((index, sentence, item, match))

    if not candidates:
        return []

    rng = random.Random(uuid.uuid4().int)
    # Spread selected contexts across the entire transcript instead of taking
    # the first five usable sentences. Group candidates by transcript position.
    candidates.sort(key=lambda item: item[0])
    by_context = {}
    for candidate in candidates:
        by_context.setdefault(candidate[0], []).append(candidate)
    contexts = list(by_context.values())
    if len(contexts) > count:
        selected_contexts = []
        for slot in range(count):
            left = round(slot * len(contexts) / count)
            right = max(left + 1, round((slot + 1) * len(contexts) / count))
            window = contexts[left:right]
            if window:
                selected_contexts.append(rng.choice(window))
    else:
        selected_contexts = contexts[:]
    rng.shuffle(selected_contexts)

    # Different natural question stems avoid repetitive cloze/grammar exercises.
    question_stems = [
        "Which concept is being explained in this passage?",
        "Which method or idea best matches the explanation below?",
        "What concept is associated with the role described here?",
        "Identify the concept that this explanation refers to.",
        "Which term best represents the idea discussed in this passage?",
    ]

    questions, seen_this_set, used_answer_terms = [], set(), set()
    all_terms = terms[:]
    for context_group in selected_contexts:
        if len(questions) >= min(count, QUIZ_QUESTION_COUNT):
            break
        # Pick a candidate whose answer has not already been used in this set.
        available = [candidate for candidate in context_group
                     if candidate[2]["key"] not in used_answer_terms]
        if not available:
            continue
        rng.shuffle(available)
        chosen = None
        for candidate in available:
            _, sentence, answer_item, match = candidate
            # Replace the named term with a neutral reference, not a blank.
            # This preserves the explanatory context without testing English grammar.
            label_words = answer_item["label"].split()
            is_plural = bool(label_words) and label_words[-1].casefold().endswith("s") and not label_words[-1].casefold().endswith("ss")
            replace_start = match.start()
            prefix = sentence[:replace_start]
            article_match = re.search(r"\b(a|an|the)\s+$", prefix, flags=re.IGNORECASE)
            if article_match:
                replace_start = article_match.start()
            preceding = sentence[:replace_start].rstrip().casefold().split()
            previous_word = preceding[-1] if preceding else ""
            if is_plural and previous_word in {"over", "through", "during", "across"}:
                replacement = "several steps"
            elif is_plural:
                replacement = "these systems"
            else:
                replacement = "this concept"
            context = sentence[:replace_start] + replacement + sentence[match.end():]
            context = re.sub(r"\s+", " ", context).strip()
            if context:
                context = context[0].upper() + context[1:]
            stem = question_stems[len(questions) % len(question_stems)]
            question_text = f"{stem}\n\n{context}"
            fingerprint = re.sub(r"\W+", " ", question_text.casefold()).strip()
            if fingerprint not in used_questions and fingerprint not in seen_this_set:
                chosen = (answer_item, sentence, question_text, fingerprint)
                break
        if not chosen:
            continue

        answer_item, source_sentence, question_text, fingerprint = chosen
        answer = answer_item["label"]
        answer_size = answer_item["size"]
        pool = [item for item in all_terms
                if item["key"] != answer_item["key"]
                and item["key"] not in used_answer_terms
                and item["size"] == answer_size]
        if len(pool) < 3:
            pool = [item for item in all_terms
                    if item["key"] != answer_item["key"]
                    and item["key"] not in used_answer_terms]
        unique = []
        seen_labels = {answer.casefold()}
        for item in pool:
            label = item["label"]
            if label.casefold() not in seen_labels:
                unique.append(label)
                seen_labels.add(label.casefold())
            if len(unique) == 3:
                break
        if len(unique) < 3:
            continue

        options = unique + [answer]
        rng.shuffle(options)
        questions.append({
            "question": question_text,
            "options": options,
            "answer": answer,
            "source_context": source_sentence,
        })
        used_answer_terms.add(answer_item["key"])
        seen_this_set.add(fingerprint)

    return questions[:min(count, QUIZ_QUESTION_COUNT)]

def current_transcript_hash():
    return hashlib.sha256(st.session_state.transcript.encode("utf-8")).hexdigest()


def ensure_current_quiz_sets_loaded():
    transcript_hash = current_transcript_hash()
    if st.session_state.get("quiz_sets_loaded_for") == transcript_hash:
        return
    try:
        all_sets, _ = load_quiz_sets()
        current_sets = []
        for item in all_sets:
            if item.get("transcript_hash") != transcript_hash:
                continue
            # Earlier versions generated fill-in-the-blank prompts. Keep those
            # historical records in GitHub, but don't show them as current quizzes.
            old_questions = item.get("questions", [])
            is_legacy_cloze = any(
                "________" in str(q.get("question", ""))
                or "according to the video" in str(q.get("question", "")).casefold()
                for q in old_questions
            )
            if not is_legacy_cloze:
                current_sets.append(item)
        st.session_state.quiz_sets = current_sets
        st.session_state.quiz_sets_storage_error = None
    except Exception as exc:
        # Keep this session usable even when GitHub storage is temporarily unavailable.
        st.session_state.quiz_sets = [
            item for item in st.session_state.get("quiz_sets", [])
            if item.get("transcript_hash") == transcript_hash
            and not any(
                "________" in str(q.get("question", ""))
                or "according to the video" in str(q.get("question", "")).casefold()
                for q in item.get("questions", [])
            )
        ]
        st.session_state.quiz_sets_storage_error = str(exc)
    st.session_state.quiz_sets_loaded_for = transcript_hash


def create_and_store_quiz_set():
    ensure_current_quiz_sets_loaded()
    prior_questions = [question for item in st.session_state.quiz_sets for question in item.get("questions", [])]
    questions = generate_overall_questions(st.session_state.transcript, count=QUIZ_QUESTION_COUNT, previous_questions=prior_questions)
    if not questions:
        return None
    set_id = str(uuid.uuid4())
    record = {
        "set_id": set_id,
        "set_number": len(st.session_state.quiz_sets) + 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "transcript_hash": current_transcript_hash(),
        "video_title": st.session_state.video_title,
        "video_url": st.session_state.video_url,
        "question_count": len(questions),
        "questions": questions,
    }
    st.session_state.quiz_sets.append(record)
    st.session_state.active_quiz_set_id = set_id
    st.session_state.questions = questions
    st.session_state.quiz_result = None
    st.session_state.score = None
    st.session_state.quiz_topic = "Entire video"
    st.session_state.quiz_number += 1
    try:
        save_quiz_set(record)
        record["saved"] = True
        st.session_state.quiz_sets_storage_error = None
    except Exception as exc:
        record["saved"] = False
        st.session_state.quiz_sets_storage_error = str(exc)
    return record

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
    "transcript": "", "chunks": [], "questions": [],
    "score": None, "quiz_result": None, "quiz_number": 0, "video_url": "",
    "video_title": "", "session_started": None, "saved_attempt_ids": [],
    "quiz_sets": [], "quiz_sets_loaded_for": None, "active_quiz_set_id": None,
    "quiz_sets_storage_error": None,
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

st.sidebar.markdown('''<div style="padding:18px 4px 20px"><div style="font-size:1.35rem;font-weight:800;letter-spacing:-.04em">LearnTube AI</div>
<div style="font-size:.78rem;color:#52645c">ADAPTIVE VIDEO LEARNING</div></div>''', unsafe_allow_html=True)
st.sidebar.markdown('---')
st.sidebar.markdown('<div style="font-size:.72rem;color:#52645c;letter-spacing:.13em;font-weight:800;padding:4px 10px 9px">WORKSPACE</div>', unsafe_allow_html=True)
page = st.sidebar.radio("Navigation", ["Dashboard", "Video Learning", "Quiz", "Analytics", "About"], label_visibility="collapsed")
st.sidebar.markdown('---')
st.sidebar.markdown('<div style="padding:8px 10px;color:#52645c;font-size:.78rem;line-height:1.6">Learn smarter.<br>One video at a time.</div>', unsafe_allow_html=True)

# -------------------- Dashboard --------------------
if page == "Dashboard":
    st.markdown('''<div class="hero"><div class="eyebrow">YOUR PERSONAL LEARNING SPACE</div>
    <h1>LearnTube AI</h1><p>Turn educational videos into structured learning. Review the complete transcript, practise with overall quizzes, and follow your learning progress in one place.</p></div>''', unsafe_allow_html=True)
    st.markdown('<div class="section-label">Your learning at a glance</div><div class="section-hint">A quick summary of your current learning session.</div>', unsafe_allow_html=True)
    a,b,c,d=st.columns(4)
    a.metric("Transcript words", f"{len(st.session_state.transcript.split()):,}" if st.session_state.transcript else "—")
    b.metric("Transcript chunks",len(st.session_state.chunks))
    c.metric("Latest quiz score",f"{st.session_state.score}%" if st.session_state.score is not None else "—")
    d.metric("Learning status","In progress" if st.session_state.transcript else "Not started")
    st.markdown('<div class="section-label">Get started</div><div class="section-hint">Follow these steps to turn a video into an active learning session.</div>',unsafe_allow_html=True)
    x,y,z=st.columns(3)
    for col,no,title,copy in [(x,"01","Add a video","Paste a YouTube link or provide its transcript."),(y,"02","Review the material","The complete transcript is used as the learning source."),(z,"03","Take an overall quiz","Answer questions sampled across the entire video and review your score.")]:
        with col: st.markdown(f'<div class="step-card"><div class="step-no">STEP {no}</div><div class="step-name">{title}</div><div class="step-copy">{copy}</div></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-label">Continue learning</div>',unsafe_allow_html=True)
    left,right=st.columns([1.4,1])
    with left:
        if st.session_state.transcript:
            st.markdown(f'<div class="panel"><div class="panel-title">{st.session_state.video_title or "Current learning session"}</div><div class="panel-subtitle">{len(st.session_state.transcript.split())} words · {len(st.session_state.chunks)} chunks</div><span class="concept-chip">Transcript processed</span></div>',unsafe_allow_html=True)
        else: empty_state("No active video yet","Open Video Learning to add a YouTube video or paste a transcript.")
    with right:
        st.markdown('<div class="panel"><div class="panel-title">Learning tip</div><div class="panel-subtitle">For better quiz results, use a transcript with clear explanations and complete explanations and enough context.</div></div>',unsafe_allow_html=True)


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
        <div class="step-no">03</div><b>Prepare an overall quiz</b>
        <p style="color:#687572;font-size:.85rem">Assess understanding across the complete transcript.</p></div>''', unsafe_allow_html=True)
        st.markdown('''<div class="panel"><div class="panel-title">Supported input</div>
        <div class="panel-subtitle">YouTube captions, pasted text, or uploaded TXT/SRT/VTT files.</div>
        <span class="concept-chip">YouTube captions</span>
        <span class="concept-chip">TXT / SRT / VTT</span>
        <span class="concept-chip">Whole-video assessment</span></div>''', unsafe_allow_html=True)

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
                    if "YOUTUBE_CLOUD_BLOCK" in error_text:
                        st.error("YouTube is blocking caption requests from the Streamlit Cloud server. Your video link is valid; this is a hosting restriction, not a language or URL problem.")
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
                st.session_state.quiz_sets_loaded_for = None
                st.session_state.quiz_sets = []
                st.session_state.active_quiz_set_id = None
                st.success("Transcript processed successfully. The complete video is ready for an overall quiz.")

    if st.session_state.transcript:
        st.markdown('<div class="section-label">Transcript analysis</div><div class="section-hint">The complete transcript is ready to be assessed. No topic classification or category selection is used.</div>', unsafe_allow_html=True)
        m1, m2 = st.columns(2)
        m1.metric("Word count", f"{len(st.session_state.transcript.split()):,}")
        m2.metric("Chunks created", len(st.session_state.chunks))
        with st.expander("Read processed transcript"):
            st.write(st.session_state.transcript)

        st.markdown('<div class="section-label">Overall video quiz</div><div class="section-hint">Generate five questions covering the important ideas across the complete transcript. You can create additional sets after finishing a quiz.</div>', unsafe_allow_html=True)
        if st.button("Generate overall quiz · 5 questions", use_container_width=True, key="make_overall_quiz_video"):
            new_set = create_and_store_quiz_set()
            if new_set:
                st.success(f"Question Set {new_set['set_number']} created with {len(new_set['questions'])} questions. Open the Quiz page to begin.")
                if len(new_set["questions"]) < QUIZ_QUESTION_COUNT:
                    st.info("Only distinct, unused transcript contexts are available, so this set contains fewer than five questions.")
                if not new_set.get("saved", False):
                    st.warning("This set is available in the current session, but GitHub could not save it permanently.")
            else:
                st.warning("No additional unused question contexts were found. Add a longer transcript or process more learning material to create another set.")
        st.caption("Each set draws questions from the complete transcript. No topic categories are selected or displayed. Previously used questions are avoided where possible.")
    else:
        empty_state("Your workspace is ready", "Add a YouTube video or paste transcript text to begin.")


# -------------------- Quiz --------------------
elif page == "Quiz":
    page_header("CHECK YOUR UNDERSTANDING", "Knowledge Check", "Take an overall assessment based on the complete video transcript. Generate another set whenever you want more practice.")

    if not st.session_state.transcript:
        empty_state("No transcript available", "Go to Video Learning and process a transcript before generating a quiz.")
    else:
        ensure_current_quiz_sets_loaded()
        left, right = st.columns([1.35, 1])
        with left:
            st.markdown('<div class="panel-title">Overall video assessment</div><div class="panel-subtitle">Five questions sampled across the complete transcript. No topic selection is required.</div>', unsafe_allow_html=True)
            button_label = "Generate 5 questions" if not st.session_state.quiz_sets else "Want another 5? Generate new questions"
            if st.button(button_label, use_container_width=True, key="generate_overall_quiz_page"):
                new_set = create_and_store_quiz_set()
                if new_set:
                    st.success(f"Question Set {new_set['set_number']} is ready.")
                    if len(new_set["questions"]) < QUIZ_QUESTION_COUNT:
                        st.info(f"This set contains {len(new_set['questions'])} new questions because only that many unused transcript contexts were available.")
                    if not new_set.get("saved", False):
                        st.warning("The new set is available in this session, but permanent GitHub saving failed. Check the storage details below.")
                else:
                    st.warning("No new unused questions could be prepared from this transcript. Try a longer transcript or process another video.")
            if st.session_state.get("quiz_sets_storage_error"):
                with st.expander("Question-set storage details"):
                    st.caption("Question sets remain available in this browser session, but permanent storage could not be confirmed.")
                    st.code(st.session_state.quiz_sets_storage_error[:1000], language=None)
        with right:
            st.markdown(f'''<div class="panel"><div class="panel-title">Quiz overview</div>
            <div class="panel-subtitle">Current learning material</div>
            <div style="font-size:1.3rem;font-weight:800;color:#202b2a">Entire transcript</div>
            <p style="color:#687572;font-size:.85rem">{len(st.session_state.quiz_sets)} saved question set(s). Each new set draws from the full video and avoids previously used questions where possible.</p></div>''', unsafe_allow_html=True)

        # Let learners reopen any previously generated set instead of discarding it.
        quiz_sets = st.session_state.quiz_sets
        if quiz_sets:
            set_by_id = {item["set_id"]: item for item in quiz_sets}
            set_ids = [item["set_id"] for item in quiz_sets]
            active_id = st.session_state.get("active_quiz_set_id")
            default_index = set_ids.index(active_id) if active_id in set_ids else len(set_ids) - 1
            selected_set_id = st.selectbox(
                "Your saved question sets",
                set_ids,
                index=default_index,
                format_func=lambda sid: (
                    f"Set {set_by_id[sid].get('set_number', '?')} · "
                    f"{set_by_id[sid].get('question_count', len(set_by_id[sid].get('questions', [])))} questions · "
                    f"{set_by_id[sid].get('created_at_utc', '')[:10]}"
                ),
                key="saved_quiz_set_picker",
            )
            if selected_set_id != st.session_state.get("active_quiz_set_id"):
                selected_record = set_by_id[selected_set_id]
                st.session_state.active_quiz_set_id = selected_set_id
                st.session_state.questions = selected_record.get("questions", [])
                st.session_state.quiz_result = None
                st.session_state.score = None
                st.session_state.quiz_topic = "Entire video"
                st.session_state.quiz_number += 1
                st.rerun()

        questions = st.session_state.questions
        active_set = next((item for item in quiz_sets if item.get("set_id") == st.session_state.get("active_quiz_set_id")), None)
        if questions and not st.session_state.quiz_result:
            set_label = f"Question Set {active_set.get('set_number', '')}" if active_set else "Current question set"
            st.markdown('<div class="section-label">Your overall quiz</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="panel-title">{set_label}</div><div class="panel-subtitle">{len(questions)} question(s) · Questions cover the complete transcript.</div>', unsafe_allow_html=True)
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
                        "quiz_set_id": st.session_state.get("active_quiz_set_id"),
                        "completed_at_utc": completed_at,
                        "video_title": st.session_state.video_title,
                        "video_url": st.session_state.video_url,
                        "topic": "Entire video",
                        "score": correct,
                        "total": len(questions),
                        "percentage": percentage,
                        "questions": [
                            {"question": questions[i]["question"], "options": questions[i]["options"],
                             "correct_answer": questions[i]["answer"], "user_answer": answers[i],
                             "is_correct": answers[i] == questions[i]["answer"]
                             }
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

        if st.session_state.quiz_result and st.session_state.questions:
            result = st.session_state.quiz_result
            st.markdown('<div class="section-label">Your result</div>', unsafe_allow_html=True)
            r1, r2, r3 = st.columns(3)
            r1.metric("Correct answers", f"{result['correct']} / {result['total']}")
            r2.metric("Final score", f"{result['percentage']}%")
            r3.metric("Performance", "High" if result["percentage"] >= 80 else "Medium" if result["percentage"] >= 50 else "Needs revision")
            if result["percentage"] >= 80:
                st.success("High performance: you demonstrated strong understanding of the video overall.")
            elif result["percentage"] >= 50:
                st.info("Medium performance: review the missed ideas and try another question set.")
            else:
                st.warning("Keep practising: revisit the transcript and try another overall question set.")
            with st.expander("Review your answers", expanded=True):
                for i, question in enumerate(st.session_state.questions):
                    st.markdown(f"**Question {i + 1}:** {question['question']}")
                    st.write(f"Your answer: {result['answers'][i]}")
                    st.write(f"Correct answer: {question['answer']}")
                    if result["answers"][i] == question["answer"]:
                        st.success("Correct")
                    else:
                        st.error("Incorrect")
            if st.button("Want another 5? Generate new questions", key="another_five_after_result", use_container_width=True):
                new_set = create_and_store_quiz_set()
                if new_set:
                    st.success(f"Question Set {new_set['set_number']} created. Your previous sets have been kept.")
                    st.rerun()
                else:
                    st.warning("No additional unused question contexts were found in this transcript.")
        elif not questions:
            empty_state("No quiz prepared yet", "Generate your first five-question assessment. You can create more sets and reopen earlier ones at any time.")


# -------------------- Analytics --------------------
elif page == "Analytics":
    page_header("LEARNING INSIGHTS","Learning Analytics","Track overall video quiz attempts and review your learning progress.")
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
    st.markdown('<div class="hero"><div class="eyebrow">PROJECT OVERVIEW</div><h1 style="font-size:1.8rem">LearnTube AI</h1><p>An adaptive educational video learning platform that processes transcripts, prepares overall video quizzes, evaluates performance, and records learning progress.</p></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-label">How the platform works</div>',unsafe_allow_html=True)
    a,b,c=st.columns(3)
    for col,n,t,d in [(a,"01","Transcript processing","Add a YouTube video or paste its transcript. The text is cleaned and split into chunks."),(b,"02","Whole-video assessment","The complete transcript is used as the source for an overall quiz. No category selection is required."),(c,"03","Quiz and analytics","Generate overall video question sets, review results, and track saved attempts.")]:
        with col: st.markdown(f'<div class="step-card"><div class="step-no">STAGE {n}</div><div class="step-name">{t}</div><div class="step-copy">{d}</div></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-label">Core features</div>',unsafe_allow_html=True)
    f1,f2=st.columns(2)
    with f1: st.markdown('<div class="panel"><div class="panel-title">Video-based learning</div><div class="panel-subtitle">Transcript input, cleaning, and chunking for whole-video assessment.</div></div><div class="panel"><div class="panel-title">Overall video quizzes</div><div class="panel-subtitle">Five-question assessments across the full transcript, with additional saved sets and no topic selection.</div></div>',unsafe_allow_html=True)
    with f2: st.markdown('<div class="panel"><div class="panel-title">Performance feedback</div><div class="panel-subtitle">Score calculation, answer review, and performance-level feedback.</div></div><div class="panel"><div class="panel-title">Learning analytics</div><div class="panel-subtitle">Quiz history and score analytics stored using GitHub-backed JSON.</div></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-label">Technology stack</div>',unsafe_allow_html=True)
    st.markdown('<span class="concept-chip">Python</span><span class="concept-chip">Streamlit</span><span class="concept-chip">Pandas</span><span class="concept-chip">YouTube Transcript API</span><span class="concept-chip">Requests</span><span class="concept-chip">GitHub API</span>',unsafe_allow_html=True)
    st.caption("Developed as an academic Machine Learning project.")
