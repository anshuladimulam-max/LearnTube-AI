import streamlit as st
import pandas as pd
import re
import sqlite3
import random

st.set_page_config(page_title="LearnTube AI", page_icon="🎓", layout="wide")

# SQLite quiz history. Streamlit Community Cloud's local filesystem is temporary.
conn = sqlite3.connect("learntube.db", check_same_thread=False)
conn.execute("""
CREATE TABLE IF NOT EXISTS quiz_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT,
    score INTEGER,
    total INTEGER
)
""")
conn.commit()

CONCEPTS = [
    "Artificial Intelligence", "Machine Learning", "Deep Learning",
    "Data Science", "Neural Networks", "Linear Regression",
    "Classification", "Clustering", "Natural Language Processing",
    "Computer Vision", "Supervised Learning", "Unsupervised Learning"
]

def identify_concepts(text):
    return [c for c in CONCEPTS if c.lower() in text.lower()]

def clean_transcript(text):
    return re.sub(r"\s+", " ", text).strip()

def split_transcript(text, size=100):
    words = text.split()
    return [" ".join(words[i:i + size]) for i in range(0, len(words), size)]

def extract_transcript(video_id):
    from youtube_transcript_api import YouTubeTranscriptApi
    transcript = YouTubeTranscriptApi().fetch(video_id)
    return " ".join(item.text for item in transcript)

def generate_questions(transcript, selected, count=5):
    sentences = re.split(r"(?<=[.!?])\s+", transcript)
    contexts = []
    for sentence in sentences:
        sentence = sentence.strip()
        if selected.lower() in sentence.lower() and 8 <= len(sentence.split()) <= 55 and sentence not in contexts:
            contexts.append(sentence)
    if len(contexts) < count:
        for chunk in split_transcript(transcript, 100):
            if selected.lower() in chunk.lower() and 8 <= len(chunk.split()) <= 100 and chunk not in contexts:
                contexts.append(chunk)
    random.shuffle(contexts)
    questions = []
    distractors = [c for c in CONCEPTS if c != selected]
    for context in contexts[:count]:
        question_text = re.sub(re.escape(selected), "________", context, count=1, flags=re.IGNORECASE)
        options = random.sample(distractors, 3) + [selected]
        random.shuffle(options)
        questions.append({"question": question_text, "options": options, "answer": selected})
    return questions

st.sidebar.title("🎓 LearnTube AI")
page = st.sidebar.radio("Navigation", ["Dashboard", "Video Learning", "Quiz", "Analytics", "About"])

for key, default in {
    "transcript": "", "concepts": [], "chunks": [], "questions": [],
    "score": None, "quiz_result": None, "quiz_number": 0
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

if page == "Dashboard":
    st.title("🎓 LearnTube AI")
    st.subheader("Adaptive Learning Through Educational Videos")
    st.write("Transform educational videos into structured learning experiences with concept identification, quizzes and personalized recommendations.")
    col1, col2, col3 = st.columns(3)
    col1.metric("Concepts Identified", len(st.session_state.concepts))
    col2.metric("Transcript Chunks", len(st.session_state.chunks))
    col3.metric("Latest Quiz Score", f"{st.session_state.score}%" if st.session_state.score is not None else "Not attempted")
    st.info("Go to Video Learning to submit a video or transcript.")

elif page == "Video Learning":
    st.title("📺 Video Learning Workspace")
    url = st.text_input("Enter an educational YouTube URL")
    transcript_input = st.text_area("Or paste the video transcript here", height=200)
    if st.button("Process Video / Transcript"):
        text = transcript_input.strip()
        if not text and url:
            match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", url)
            if match:
                try:
                    with st.spinner("Extracting YouTube transcript..."):
                        text = extract_transcript(match.group(1))
                except Exception:
                    st.warning("Automatic transcript extraction failed. Please paste the transcript manually.")
            else:
                st.error("Please enter a valid YouTube URL.")
        if text:
            st.session_state.transcript = clean_transcript(text)
            st.session_state.chunks = split_transcript(st.session_state.transcript)
            st.session_state.concepts = identify_concepts(st.session_state.transcript)
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

elif page == "Quiz":
    st.title("📝 Adaptive Learning Quiz")
    if not st.session_state.transcript:
        st.warning("Process a transcript first in Video Learning.")
    elif not st.session_state.concepts:
        st.warning("No predefined concepts found. Try another transcript.")
    else:
        st.write("Select a topic and answer questions based on your video transcript.")
        selected = st.selectbox("Select Learning Topic", st.session_state.concepts)
        if st.button("Generate 5 Questions"):
            questions = generate_questions(st.session_state.transcript, selected, count=5)
            if questions:
                st.session_state.questions = questions
                st.session_state.quiz_topic = selected
                st.session_state.quiz_result = None
                st.session_state.quiz_number += 1
                st.success(f"{len(questions)} questions generated successfully!")
            else:
                st.warning("No suitable transcript contexts found for this topic. Try another topic.")
        if st.session_state.questions:
            questions = st.session_state.questions
            st.divider()
            st.subheader(f"Topic: {st.session_state.quiz_topic}")
            st.write(f"Total Questions: {len(questions)}")
            with st.form(f"quiz_form_{st.session_state.quiz_number}"):
                answers = []
                for i, q in enumerate(questions):
                    st.markdown(f"### Question {i + 1}")
                    st.write(q["question"])
                    answer = st.radio("Choose your answer:", q["options"], index=None, key=f"quiz_{st.session_state.quiz_number}_{i}")
                    answers.append(answer)
                    st.divider()
                submitted = st.form_submit_button("Submit Quiz")
            if submitted:
                if any(answer is None for answer in answers):
                    st.warning("Please answer all questions before submitting.")
                else:
                    correct = sum(answers[i] == questions[i]["answer"] for i in range(len(questions)))
                    percentage = round(correct / len(questions) * 100)
                    st.session_state.quiz_result = {"answers": answers, "correct": correct, "total": len(questions), "percentage": percentage}
                    st.session_state.score = percentage
                    conn.execute("INSERT INTO quiz_history(topic, score, total) VALUES (?, ?, ?)", (st.session_state.quiz_topic, correct, len(questions)))
                    conn.commit()
            if st.session_state.quiz_result:
                result = st.session_state.quiz_result
                st.divider()
                st.subheader("📊 Quiz Results")
                st.metric("Your Score", f"{result['correct']}/{result['total']}")
                st.metric("Percentage", f"{result['percentage']}%")
                st.subheader("Answer Review")
                for i, q in enumerate(questions):
                    st.markdown(f"**Question {i + 1}**")
                    st.write(q["question"])
                    st.write(f"Your Answer: {result['answers'][i]}")
                    st.write(f"Correct Answer: {q['answer']}")
                    st.success("Correct answer!") if result["answers"][i] == q["answer"] else st.error("Incorrect answer.")
                if result["percentage"] >= 80:
                    st.success("High Performance: Try advanced learning material.")
                elif result["percentage"] >= 50:
                    st.info("Medium Performance: Revise this concept and practise again.")
                else:
                    st.warning("Low Performance: Revisit the lesson and practise basic questions.")

elif page == "Analytics":
    st.title("📊 Learning Analytics")
    history = pd.read_sql_query("SELECT * FROM quiz_history ORDER BY id DESC", conn)
    if history.empty:
        st.info("Your quiz history will appear here after your first attempt.")
    else:
        st.dataframe(history, use_container_width=True)
        st.bar_chart(history.set_index("id")["score"])
        st.metric("Total Quiz Attempts", len(history))
        st.metric("Average Score", f"{history['score'].mean():.1f}%")

elif page == "About":
    st.title("About LearnTube AI")
    st.write("""
    LearnTube AI is an adaptive educational video learning platform.

    Its main objectives are:
    - Process educational video transcripts.
    - Identify important learning concepts.
    - Support concept-based quizzes.
    - Evaluate student performance.
    - Recommend learning activities.
    - Track learning progress.
    """)
    st.write("Developed as an academic Machine Learning project.")
