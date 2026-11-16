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

    sentences = re.split(r"(?<=[.!?])\\s+", transcript)
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
    "transcript": "",
    "concepts": [],
    "chunks": [],
    "questions": [],
    "score": None,
    "quiz_result": None,
    "quiz_number": 0,
    "video_url": "",
    "video_title": "",
    "session_started": None,
    "saved_attempt_ids": [],
}
for key, default in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = default

st.sidebar.title("🎓 LearnTube AI")
page = st.sidebar.radio(
    "Navigation",
    ["Dashboard", "Video Learning", "Quiz", "Analytics", "About"]
)

# -------------------- Dashboard --------------------
if page == "Dashboard":
    st.title("🎓 LearnTube AI")
    st.subheader("Adaptive Learning Through Educational Videos")
    st.write(
        "Transform educational videos into structured learning experiences "
        "with concept identification, quizzes and personalized recommendations."
    )
    col1, col2, col3 = st.columns(3)
    col1.metric("Concepts Identified", len(st.session_state.concepts))
    col2.metric("Transcript Chunks", len(st.session_state.chunks))
    col3.metric(
        "Latest Quiz Score",
        f"{st.session_state.score}%" if st.session_state.score is not None
        else "Not attempted"
    )
    st.info("Go to Video Learning to submit a video or transcript.")

# -------------------- Video learning --------------------
elif page == "Video Learning":
    st.title("📺 Video Learning Workspace")
    url = st.text_input(
        "Enter an educational YouTube URL",
        value=st.session_state.video_url
    )
    transcript_input = st.text_area(
        "Or paste the video transcript here", height=200
    )

    if st.button("Process Video / Transcript"):
        text = transcript_input.strip()
        if not text and url:
            video_id = youtube_video_id(url)
            if video_id:
                try:
                    with st.spinner("Extracting YouTube transcript..."):
                        text = extract_transcript(video_id)
                except Exception as exc:
                    st.warning(
                        "Automatic transcript extraction failed. "
                        "Please paste the transcript manually."
                    )
            else:
                st.error("Please enter a valid YouTube URL.")

        if text:
            st.session_state.transcript = clean_transcript(text)
            st.session_state.chunks = split_transcript(st.session_state.transcript)
            st.session_state.concepts = identify_concepts(st.session_state.transcript)
            st.session_state.video_url = url.strip()
            st.session_state.video_title = (
                f"YouTube video {youtube_video_id(url)}"
                if youtube_video_id(url) else "Pasted transcript"
            )
            st.session_state.session_started = datetime.now(timezone.utc).isoformat()
            st.session_state.questions = []
            st.session_state.quiz_result = None
            st.session_state.score = None
            st.success("Transcript processed successfully!")

    if st.session_state.transcript:
        st.subheader("Transcript Analysis")
        st.write("Word count:", len(st.session_state.transcript.split()))
        st.write("Chunks created:", len(st.session_state.chunks))
        st.subheader("Identified Concepts")
        if st.session_state.concepts:
            st.write(st.session_state.concepts)
        else:
            st.warning("No predefined concepts found in this transcript.")
        with st.expander("View processed transcript"):
            st.write(st.session_state.transcript)

# -------------------- Quiz --------------------
elif page == "Quiz":
    st.title("📝 Adaptive Learning Quiz")
    st.caption("The quiz generates up to 5 questions, depending on transcript content.")

    if not st.session_state.transcript:
        st.warning("Process a transcript first in Video Learning.")
    elif not st.session_state.concepts:
        st.warning("No predefined concepts found. Try another transcript.")
    else:
        st.write("Select a topic and answer questions based on your video transcript.")
        selected = st.selectbox("Select Learning Topic", st.session_state.concepts)

        if st.button("Generate 5 Questions"):
            questions = generate_questions(
                st.session_state.transcript,
                selected,
                count=QUIZ_QUESTION_COUNT
            )
            if questions:
                st.session_state.questions = questions
                st.session_state.quiz_topic = selected
                st.session_state.quiz_result = None
                st.session_state.quiz_number += 1
                if len(questions) == QUIZ_QUESTION_COUNT:
                    st.success("5 questions generated successfully!")
                else:
                    st.info(
                        f"Only {len(questions)} suitable question(s) were found "
                        "in this transcript. A partial quiz has been created."
                    )
            else:
                st.session_state.questions = []
                st.session_state.quiz_result = None
                st.warning(
                    "No suitable questions could be created for this topic. "
                    "Please use a longer transcript or select another topic."
                )

        questions = st.session_state.questions
        if questions:
            st.divider()
            st.subheader(f"Topic: {st.session_state.quiz_topic}")
            total_questions = len(questions)
            st.write(f"Total Questions: {total_questions}")

            with st.form(f"quiz_form_{st.session_state.quiz_number}"):
                answers = []
                for i, question in enumerate(questions):
                    st.markdown(f"### Question {i + 1}")
                    st.write(question["question"])
                    answer = st.radio(
                        "Choose your answer:",
                        question["options"],
                        index=None,
                        key=f"quiz_{st.session_state.quiz_number}_{i}"
                    )
                    answers.append(answer)
                    st.divider()
                submitted = st.form_submit_button("Submit Quiz")

            if submitted:
                if any(answer is None for answer in answers):
                    st.warning(
                        f"Please answer all {total_questions} question(s) before submitting."
                    )
                else:
                    correct = sum(
                        answers[i] == questions[i]["answer"]
                        for i in range(total_questions)
                    )
                    percentage = round(correct / total_questions * 100)
                    attempt_id = str(uuid.uuid4())
                    completed_at = datetime.now(timezone.utc).isoformat()

                    result = {
                        "answers": answers,
                        "correct": correct,
                        "total": total_questions,
                        "percentage": percentage,
                        "attempt_id": attempt_id,
                    }
                    st.session_state.quiz_result = result
                    st.session_state.score = percentage

                    attempt = {
                        "attempt_id": attempt_id,
                        "completed_at_utc": completed_at,
                        "video_title": st.session_state.video_title,
                        "video_url": st.session_state.video_url,
                        "topic": st.session_state.quiz_topic,
                        "score": correct,
                        "total": total_questions,
                        "percentage": percentage,
                        "questions": [
                            {
                                "question": questions[i]["question"],
                                "options": questions[i]["options"],
                                "correct_answer": questions[i]["answer"],
                                "user_answer": answers[i],
                                "is_correct": answers[i] == questions[i]["answer"],
                            }
                            for i in range(total_questions)
                        ],
                    }

                    try:
                        with st.spinner("Saving your quiz attempt to GitHub..."):
                            save_attempt(attempt)
                        st.session_state.saved_attempt_ids.append(attempt_id)
                        st.success("Quiz result saved permanently to GitHub analytics.")
                    except Exception as exc:
                        st.error(
                            "Your quiz was scored, but permanent saving failed. "
                            "Do not assume this attempt is stored. "
                            f"Storage error: {exc}"
                        )

            if st.session_state.quiz_result:
                result = st.session_state.quiz_result
                st.divider()
                st.subheader("📊 Quiz Results")
                st.metric("Your Score", f"{result['correct']}/{result['total']}")
                st.metric("Percentage", f"{result['percentage']}%")
                st.subheader("Answer Review")
                for i, question in enumerate(questions):
                    st.markdown(f"**Question {i + 1}**")
                    st.write(question["question"])
                    st.write(f"Your Answer: {result['answers'][i]}")
                    st.write(f"Correct Answer: {question['answer']}")
                    if result["answers"][i] == question["answer"]:
                        st.success("Correct answer!")
                    else:
                        st.error("Incorrect answer.")

                if result["percentage"] >= 80:
                    st.success("High Performance: Try advanced learning material.")
                elif result["percentage"] >= 50:
                    st.info("Medium Performance: Revise this concept and practise again.")
                else:
                    st.warning("Low Performance: Revisit the lesson and practise basic questions.")

# -------------------- Analytics --------------------
elif page == "Analytics":
    st.title("📊 Learning Analytics")
    st.caption("History is loaded from the GitHub repository, not temporary app storage.")

    try:
        history_records, _ = load_history()
        if not history_records:
            st.info("Your quiz history will appear here after your first saved attempt.")
        else:
            history = pd.DataFrame(history_records)
            if "completed_at_utc" in history.columns:
                history["completed_at_utc"] = pd.to_datetime(
                    history["completed_at_utc"], errors="coerce", utc=True
                )
                history = history.sort_values("completed_at_utc", ascending=False)

            col1, col2, col3 = st.columns(3)
            col1.metric("Total Quiz Attempts", len(history))
            col2.metric("Average Score", f"{history['percentage'].mean():.1f}%")
            col3.metric("Total Questions Answered", int(history["total"].sum()))

            st.subheader("Quiz History")
            display_columns = [
                column for column in [
                    "completed_at_utc", "video_title", "topic",
                    "score", "total", "percentage"
                ] if column in history.columns
            ]
            st.dataframe(history[display_columns], use_container_width=True)

            st.subheader("Score History")
            chart_data = history.copy()
            if "completed_at_utc" in chart_data.columns:
                chart_data = chart_data.sort_values("completed_at_utc")
                chart_data = chart_data.set_index("completed_at_utc")
            st.line_chart(chart_data["percentage"])

            if "topic" in history.columns:
                st.subheader("Performance by Topic")
                topic_average = history.groupby("topic")["percentage"].mean()
                st.bar_chart(topic_average)

            with st.expander("View complete saved attempt records"):
                st.json(history_records)

    except Exception as exc:
        st.error(f"Unable to load permanent analytics from GitHub: {exc}")
        st.info(
            "Check that GITHUB_TOKEN is configured in Streamlit Secrets and "
            "has Contents read/write permission for this repository."
        )

# -------------------- About --------------------
elif page == "About":
    st.title("About LearnTube AI")
    st.write("""
    LearnTube AI is an adaptive educational video learning platform.

    Its main objectives are:
    - Process educational video transcripts.
    - Identify important learning concepts.
    - Support concept-based quizzes.
    - Generate up to five transcript-based questions per quiz.
    - Evaluate student performance.
    - Track learning progress using persistent GitHub-backed analytics.
    """)
    st.write("Developed as an academic Machine Learning project.")
