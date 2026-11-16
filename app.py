import streamlit as st
import pandas as pd
import re
import random
import json
import uuid
import hashlib
import requests
from datetime import datetime, timezone

st.set_page_config(page_title="LearnTube AI", page_icon="🎓", layout="wide")

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
:root { --lt-ink:#172554; --lt-muted:#64748b; --lt-blue:#3157d5; --lt-purple:#7656d6; --lt-line:#e5eaf4; }
html, body, [class*="css"] { font-family:'DM Sans', sans-serif; }
.stApp { background:linear-gradient(180deg,#f7f9ff 0%,#ffffff 360px); color:#172033; }
.block-container { max-width:1180px; padding-top:2.1rem; padding-bottom:3rem; }
[data-testid="stSidebar"] { background:linear-gradient(180deg,#111d42 0%,#1d2d60 100%); border-right:0; }
[data-testid="stSidebar"] * { color:#f8faff !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] label { padding:9px 12px; border-radius:10px; }
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover { background:#ffffff18; }
[data-testid="stSidebar"] hr { border-color:#ffffff30; }
[data-testid="stMetric"] { background:#fff; border:1px solid var(--lt-line); border-radius:16px; padding:18px 20px; box-shadow:0 5px 18px #1e3a8a0a; }
[data-testid="stMetricLabel"] { color:#64748b; font-weight:600; }
[data-testid="stMetricValue"] { color:var(--lt-ink); font-weight:800; }
.stButton > button, .stFormSubmitButton > button { border:0; border-radius:11px; padding:.62rem 1.2rem; font-weight:700; color:white; background:linear-gradient(100deg,var(--lt-blue),var(--lt-purple)); box-shadow:0 5px 14px #3157d52b; transition:transform .15s ease, box-shadow .15s ease; }
.stButton > button:hover, .stFormSubmitButton > button:hover { color:white; transform:translateY(-1px); box-shadow:0 8px 18px #3157d540; }
input, textarea, [data-baseweb="select"] > div { border-radius:10px !important; }
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea { border:1px solid #dce3f0; background:#fff; }
[data-testid="stExpander"] { border:1px solid var(--lt-line); border-radius:12px; background:#fff; }
[data-testid="stDataFrame"] { border:1px solid var(--lt-line); border-radius:12px; overflow:hidden; }
hr { border-color:var(--lt-line); }
h1 { color:#172554; font-weight:800; letter-spacing:-.04em; }
h2, h3 { color:#20315d; font-weight:700; letter-spacing:-.02em; }
.lt-hero { background:linear-gradient(115deg,#172554 0%,#344db1 58%,#7656d6 100%); border-radius:22px; padding:30px 32px; color:#fff; margin:0 0 24px 0; box-shadow:0 12px 32px #243b8b25; }
.lt-hero h1 { color:#fff !important; font-size:2.15rem; margin:0 0 8px 0; }
.lt-hero p { color:#e8edff; font-size:1.02rem; margin:0; max-width:760px; }
.lt-eyebrow { color:#cbd5ff; font-size:.76rem; font-weight:800; letter-spacing:.13em; text-transform:uppercase; margin-bottom:10px; }
.lt-section { background:#fff; border:1px solid var(--lt-line); border-radius:16px; padding:18px 20px; margin:10px 0; box-shadow:0 5px 18px #1e3a8a08; }
.lt-pill { display:inline-block; background:#edf2ff; color:#3157d5; padding:5px 10px; border-radius:999px; font-size:.78rem; font-weight:700; margin:3px 5px 3px 0; }
@media (max-width: 700px) { .block-container { padding:1rem 1rem 2rem; } .lt-hero { padding:23px; } .lt-hero h1 { font-size:1.65rem; } }
</style>
""", unsafe_allow_html=True)

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
CONCEPTS = [
    "Artificial Intelligence", "Machine Learning", "Deep Learning",
    "Data Science", "Neural Networks", "Linear Regression",
    "Classification", "Clustering", "Natural Language Processing",
    "Computer Vision", "Supervised Learning", "Unsupervised Learning"
]

QUIZ_QUESTION_COUNT = 5


def identify_concepts(text):
    return [concept for concept in CONCEPTS if concept.lower() in text.lower()]


def clean_transcript(text):
    return re.sub(r"\s+", " ", text).strip()


def split_transcript(text, size=100):
    words = text.split()
    return [" ".join(words[i:i + size]) for i in range(0, len(words), size)]


def extract_transcript(video_id):
    from youtube_transcript_api import YouTubeTranscriptApi
    transcript = YouTubeTranscriptApi().fetch(video_id)
    return " ".join(item.text for item in transcript)


def generate_questions(transcript, selected, count=QUIZ_QUESTION_COUNT):
    # Aim for five questions, but return a smaller valid quiz when the
    # transcript contains fewer suitable, distinct contexts.
    if count < 1:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", transcript)
    contexts = []
    seen = set()

    for sentence in sentences:
        sentence = sentence.strip()
        words = sentence.split()
        normalized = sentence.casefold()
        if (
            selected.casefold() in normalized
            and 8 <= len(words) <= 55
            and normalized not in seen
        ):
            contexts.append(sentence)
            seen.add(normalized)

    if len(contexts) < count:
        for chunk in split_transcript(transcript, 100):
            normalized = chunk.casefold()
            if (
                selected.casefold() in normalized
                and 8 <= len(chunk.split()) <= 100
                and normalized not in seen
            ):
                contexts.append(chunk)
                seen.add(normalized)

    random.shuffle(contexts)
    distractors = [concept for concept in CONCEPTS if concept != selected]
    questions = []

    for context in contexts[:count]:
        question_text = re.sub(
            re.escape(selected), "________", context,
            count=1, flags=re.IGNORECASE
        )
        options = random.sample(distractors, 3) + [selected]
        random.shuffle(options)
        questions.append({
            "question": question_text,
            "options": options,
            "answer": selected,
        })

    return questions


def youtube_video_id(url):
    match = re.search(
        r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})",
        url or ""
    )
    return match.group(1) if match else None


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

st.markdown('''<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
:root{--ink:#172554;--muted:#64748b;--blue:#3657d6;--purple:#7957d5;--line:#e7ebf3;}
html,body,[class*="css"]{font-family:'DM Sans',sans-serif}
.stApp{background:#f6f8fc;color:#172033}
.block-container{max-width:1240px;padding-top:2rem;padding-bottom:3rem}
[data-testid="stHeader"]{background:rgba(246,248,252,.88)}
[data-testid="stSidebar"]{background:#111d3d;border-right:1px solid #25345c}
[data-testid="stSidebar"] *{color:#f8faff}
[data-testid="stSidebar"] [data-testid="stRadio"] label{padding:11px 13px;border-radius:11px;font-weight:600}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover{background:#ffffff16}
[data-testid="stSidebar"] hr{border-color:#ffffff24}
[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:15px;padding:17px 19px;box-shadow:0 5px 18px #17255408}
[data-testid="stMetricLabel"]{color:#64748b;font-weight:600}
[data-testid="stMetricValue"]{color:var(--ink);font-weight:800}
.stButton>button,.stFormSubmitButton>button{border:0;border-radius:10px;padding:.62rem 1.15rem;font-weight:700;color:#fff;background:linear-gradient(100deg,var(--blue),var(--purple));box-shadow:0 5px 13px #3657d626}
.stButton>button:hover,.stFormSubmitButton>button:hover{color:white;border:0;transform:translateY(-1px)}
[data-testid="stTextInput"] input,[data-testid="stTextArea"] textarea,[data-baseweb="select"]>div{border:1px solid #dce3ef;border-radius:10px;background:#fff}
[data-testid="stExpander"], [data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:12px;background:#fff}
hr{border-color:var(--line)}
h1,h2,h3{color:var(--ink);letter-spacing:-.035em}
.page-head{margin:0 0 22px}.kicker{color:#5369c8;font-size:.72rem;font-weight:800;letter-spacing:.15em;text-transform:uppercase;margin-bottom:7px}
.page-head h1{font-size:2.05rem;font-weight:800;margin:0 0 5px}.page-head p{color:#64748b;margin:0;font-size:.98rem}
.hero{background:linear-gradient(115deg,#172554 0%,#3049a4 58%,#7957d5 100%);padding:32px 34px;border-radius:22px;color:#fff;box-shadow:0 15px 35px #243b8b20;margin-bottom:22px}
.hero .eyebrow{font-size:.72rem;letter-spacing:.16em;font-weight:800;color:#dbe4ff;text-transform:uppercase}.hero h1{color:#fff;font-size:2.25rem;margin:9px 0 7px;font-weight:800}.hero p{color:#edf1ff;max-width:720px;margin:0;font-size:1rem;line-height:1.6}
.section-label{font-size:1.12rem;font-weight:800;color:#172554;margin:24px 0 4px}.section-hint{font-size:.87rem;color:#748098;margin-bottom:13px}
.panel{background:#fff;border:1px solid var(--line);border-radius:16px;padding:19px 20px;margin:10px 0;box-shadow:0 5px 18px #17255407}
.panel-title{font-weight:800;color:#172554;font-size:1.02rem;margin-bottom:5px}.panel-subtitle{color:#748098;font-size:.86rem;margin-bottom:12px}
.empty-state{background:#fff;border:1px dashed #cbd5e1;border-radius:16px;padding:32px 20px;text-align:center;margin:15px 0}.empty-icon{font-size:1.7rem;color:#6478d8}.empty-title{font-size:1.05rem;font-weight:800;color:#172554;margin:7px}.empty-copy{color:#748098;font-size:.9rem;max-width:560px;margin:auto}
.concept-chip{display:inline-block;padding:8px 12px;border-radius:9px;background:#eef2ff;color:#354db0;font-weight:700;font-size:.82rem;margin:4px 5px 4px 0;border:1px solid #e0e7ff}
.step-card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;height:100%}.step-no{font-size:.72rem;font-weight:800;color:#6478d8;letter-spacing:.12em}.step-name{font-size:.98rem;font-weight:800;color:#172554;margin:7px 0 4px}.step-copy{font-size:.83rem;color:#748098;line-height:1.5}
@media(max-width:700px){.block-container{padding:1rem}.hero{padding:23px}.hero h1{font-size:1.7rem}.page-head h1{font-size:1.7rem}}
</style>''', unsafe_allow_html=True)

st.sidebar.markdown('''<div style="padding:18px 4px 20px"><div style="font-size:2.1rem">🎓</div>
<div style="font-size:1.35rem;font-weight:800;letter-spacing:-.04em">LearnTube AI</div>
<div style="font-size:.78rem;color:#bdc9e8">ADAPTIVE VIDEO LEARNING</div></div>''', unsafe_allow_html=True)
st.sidebar.markdown('---')
st.sidebar.markdown('<div style="font-size:.72rem;color:#9eadd0;letter-spacing:.13em;font-weight:800;padding:4px 10px 9px">WORKSPACE</div>', unsafe_allow_html=True)
page = st.sidebar.radio("Navigation", ["Dashboard", "Video Learning", "Quiz", "Analytics", "About"], label_visibility="collapsed")
st.sidebar.markdown('---')
st.sidebar.markdown('<div style="padding:8px 10px;color:#aab8d8;font-size:.78rem;line-height:1.6">Learn smarter.<br>One video at a time.</div>', unsafe_allow_html=True)

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
    for col,no,title,copy in [(x,"01","Add a video","Paste a YouTube link or provide its transcript."),(y,"02","Explore concepts","Review the supported concepts identified from the transcript."),(z,"03","Take a quiz","Answer topic-based questions and review your score.")]:
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
    page_header("LEARNING WORKSPACE","Video Learning","Bring an educational video into your learning workspace and explore its transcript.")
    l,r=st.columns([1.25,.75],gap="large")
    with l:
        st.markdown('<div class="panel-title">01 · Add your learning material</div><div class="panel-subtitle">Use a YouTube URL with an available transcript, or paste transcript text directly.</div>',unsafe_allow_html=True)
        url=st.text_input("YouTube video URL",value=st.session_state.video_url,placeholder="https://www.youtube.com/watch?v=...")
        st.markdown('<div style="text-align:center;color:#94a3b8;font-size:.8rem;padding:2px">— OR PASTE TRANSCRIPT —</div>',unsafe_allow_html=True)
        transcript_input=st.text_area("Transcript text",height=230,placeholder="Paste the video transcript here...")
        process=st.button("✦  Process learning material",use_container_width=True)
    with r:
        st.markdown('<div class="panel"><div class="panel-title">How it works</div><div class="panel-subtitle">Your transcript is processed in three stages.</div><div class="step-no">01</div><b>Clean transcript</b><p style="color:#748098;font-size:.85rem">Normalize the text for analysis.</p><div class="step-no">02</div><b>Split into chunks</b><p style="color:#748098;font-size:.85rem">Divide transcript into manageable sections.</p><div class="step-no">03</div><b>Identify concepts</b><p style="color:#748098;font-size:.85rem">Find supported learning topics.</p></div>',unsafe_allow_html=True)
        st.markdown('<div class="panel"><div class="panel-title">Supported input</div><div class="panel-subtitle">YouTube links and manually pasted transcript text.</div><span class="concept-chip">YouTube</span><span class="concept-chip">Transcript</span><span class="concept-chip">Concept analysis</span></div>',unsafe_allow_html=True)
    if process:
        text=transcript_input.strip()
        if not text and url:
            video_id=youtube_video_id(url)
            if video_id:
                try:
                    with st.spinner("Extracting YouTube transcript..."): text=extract_transcript(video_id)
                except Exception:
                    st.warning("Automatic transcript extraction failed. Please paste the transcript manually.")
            else: st.error("Please enter a valid YouTube URL.")
        if text:
            st.session_state.transcript=clean_transcript(text)
            st.session_state.chunks=split_transcript(st.session_state.transcript)
            st.session_state.concepts=identify_concepts(st.session_state.transcript)
            st.session_state.video_url=url.strip()
            st.session_state.video_title=f"YouTube video {youtube_video_id(url)}" if youtube_video_id(url) else "Pasted transcript"
            st.session_state.session_started=datetime.now(timezone.utc).isoformat()
            st.session_state.questions=[]; st.session_state.quiz_result=None; st.session_state.score=None
            st.success("Transcript processed successfully!")
    if st.session_state.transcript:
        st.markdown('<div class="section-label">Transcript analysis</div><div class="section-hint">Review the processing summary and concepts found.</div>',unsafe_allow_html=True)
        m1,m2,m3=st.columns(3);m1.metric("Word count",f"{len(st.session_state.transcript.split()):,}");m2.metric("Chunks created",len(st.session_state.chunks));m3.metric("Concepts identified",len(st.session_state.concepts))
        st.markdown('<div class="panel-title">Identified concepts</div>',unsafe_allow_html=True)
        if st.session_state.concepts:
            st.markdown(''.join(f'<span class="concept-chip">{c}</span>' for c in st.session_state.concepts),unsafe_allow_html=True)
        else: st.warning("No predefined concepts found in this transcript.")
        with st.expander("Read processed transcript"): st.write(st.session_state.transcript)
    else: empty_state("Your workspace is ready","Add a YouTube video or paste transcript text to begin.")

# -------------------- Quiz --------------------
elif page == "Quiz":
    page_header("CHECK YOUR UNDERSTANDING","Knowledge Check","Practise with questions based on concepts found in your processed transcript.")
    if not st.session_state.transcript:
        empty_state("No transcript available","Go to Video Learning and process a video transcript before generating a quiz.")
    elif not st.session_state.concepts:
        empty_state("No supported concepts found","Try another transcript containing one or more supported learning concepts.")
    else:
        top1,top2=st.columns([1.4,1])
        with top1:
            st.markdown('<div class="panel-title">Choose your quiz topic</div><div class="panel-subtitle">Select one of the concepts identified in your transcript.</div>',unsafe_allow_html=True)
            selected=st.selectbox("Learning topic",st.session_state.concepts,label_visibility="collapsed")
            if st.button("Generate quiz questions",use_container_width=True):
                questions=generate_questions(st.session_state.transcript,selected,count=QUIZ_QUESTION_COUNT)
                if questions:
                    st.session_state.questions=questions;st.session_state.quiz_topic=selected;st.session_state.quiz_result=None;st.session_state.quiz_number+=1
                    if len(questions)==QUIZ_QUESTION_COUNT: st.success("Your quiz is ready!")
                    else: st.info(f"Only {len(questions)} suitable question(s) were found. A shorter quiz is ready.")
                else:
                    st.session_state.questions=[];st.session_state.quiz_result=None
                    st.warning("No suitable questions could be created. Use a longer transcript or select another topic.")
        with top2:
            st.markdown(f'<div class="panel"><div class="panel-title">Quiz overview</div><div class="panel-subtitle">Your selected learning material</div><div style="font-size:1.3rem;font-weight:800;color:#263b85">{len(st.session_state.concepts)} topics</div><p style="color:#748098;font-size:.85rem">Up to five questions per quiz, depending on available transcript context.</p></div>',unsafe_allow_html=True)
        questions=st.session_state.questions
        if questions:
            st.markdown('<div class="section-label">Your quiz</div>',unsafe_allow_html=True)
            st.markdown(f'<div class="panel-title">{st.session_state.quiz_topic}</div><div class="panel-subtitle">{len(questions)} question(s) · Select one answer for each question.</div>',unsafe_allow_html=True)
            with st.form(f"quiz_form_{st.session_state.quiz_number}"):
                answers=[]
                for i,q in enumerate(questions):
                    st.markdown(f'''<div class="panel-title" style="margin-top:8px">QUESTION {i+1:02d} <span style="color:#94a3b8;font-weight:500">/ {len(questions):02d}</span></div>''',unsafe_allow_html=True)
                    st.progress((i+1)/len(questions))
                    st.markdown(f'<div style="font-size:1rem;font-weight:600;color:#24345e;margin:8px 0 12px">{q["question"]}</div>',unsafe_allow_html=True)
                    ans=st.radio("Choose your answer",q["options"],index=None,key=f"quiz_{st.session_state.quiz_number}_{i}",label_visibility="collapsed")
                    answers.append(ans);st.divider()
                submitted=st.form_submit_button("Submit answers",use_container_width=True)
            if submitted:
                if any(a is None for a in answers): st.warning(f"Please answer all {len(questions)} questions before submitting.")
                else:
                    correct=sum(answers[i]==questions[i]["answer"] for i in range(len(questions)))
                    percentage=round(correct/len(questions)*100);attempt_id=str(uuid.uuid4());completed_at=datetime.now(timezone.utc).isoformat()
                    result={"answers":answers,"correct":correct,"total":len(questions),"percentage":percentage,"attempt_id":attempt_id}
                    st.session_state.quiz_result=result;st.session_state.score=percentage
                    attempt={"attempt_id":attempt_id,"completed_at_utc":completed_at,"video_title":st.session_state.video_title,"video_url":st.session_state.video_url,"topic":st.session_state.quiz_topic,"score":correct,"total":len(questions),"percentage":percentage,"questions":[{"question":questions[i]["question"],"options":questions[i]["options"],"correct_answer":questions[i]["answer"],"user_answer":answers[i],"is_correct":answers[i]==questions[i]["answer"]} for i in range(len(questions))]}
                    try:
                        with st.spinner("Saving your quiz attempt to GitHub..."): save_attempt(attempt)
                        st.session_state.saved_attempt_ids.append(attempt_id);st.success("Quiz result saved to GitHub analytics.")
                    except Exception as exc: st.error(f"Your quiz was scored, but permanent saving failed: {exc}")
            if st.session_state.quiz_result:
                result=st.session_state.quiz_result
                st.markdown('<div class="section-label">Your result</div>',unsafe_allow_html=True)
                r1,r2,r3=st.columns(3);r1.metric("Correct answers",f"{result['correct']} / {result['total']}");r2.metric("Final score",f"{result['percentage']}%");r3.metric("Performance","High" if result['percentage']>=80 else "Medium" if result['percentage']>=50 else "Needs revision")
                if result['percentage']>=80: st.success("High Performance: Try advanced learning material.")
                elif result['percentage']>=50: st.info("Medium Performance: Revise this concept and practise again.")
                else: st.warning("Keep practising: revisit the lesson and review the concepts.")
                with st.expander("Review your answers",expanded=True):
                    for i,q in enumerate(questions):
                        st.markdown(f"**Question {i+1}:** {q['question']}")
                        st.write(f"Your answer: {result['answers'][i]} · Correct answer: {q['answer']}")
                        st.success("Correct") if result['answers'][i]==q['answer'] else st.error("Incorrect")

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
    for col,n,t,d in [(a,"01","Transcript processing","Add a YouTube video or paste its transcript. The text is cleaned and split into chunks."),(b,"02","Concept identification","The application checks the transcript for supported learning concepts."),(c,"03","Quiz and analytics","Generate topic-based questions, review your result, and track saved attempts.")]:
        with col: st.markdown(f'<div class="step-card"><div class="step-no">STAGE {n}</div><div class="step-name">{t}</div><div class="step-copy">{d}</div></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-label">Core features</div>',unsafe_allow_html=True)
    f1,f2=st.columns(2)
    with f1: st.markdown('<div class="panel"><div class="panel-title">Video-based learning</div><div class="panel-subtitle">Transcript input, cleaning, chunking, and supported concept identification.</div></div><div class="panel"><div class="panel-title">Concept-based quizzes</div><div class="panel-subtitle">Up to five transcript-context questions for a selected concept.</div></div>',unsafe_allow_html=True)
    with f2: st.markdown('<div class="panel"><div class="panel-title">Performance feedback</div><div class="panel-subtitle">Score calculation, answer review, and performance-level feedback.</div></div><div class="panel"><div class="panel-title">Learning analytics</div><div class="panel-subtitle">Quiz history and score analytics stored using GitHub-backed JSON.</div></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-label">Technology stack</div>',unsafe_allow_html=True)
    st.markdown('<span class="concept-chip">Python</span><span class="concept-chip">Streamlit</span><span class="concept-chip">Pandas</span><span class="concept-chip">YouTube Transcript API</span><span class="concept-chip">Requests</span><span class="concept-chip">GitHub API</span>',unsafe_allow_html=True)
    st.caption("Developed as an academic Machine Learning project.")
