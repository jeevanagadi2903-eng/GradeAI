import os
import io
import re
import time
import base64

import fitz  # pymupdf
from groq import Groq, BadRequestError, RateLimitError
from dotenv import load_dotenv

load_dotenv()

# Models are read from environment variables so the next Groq deprecation is a
# Render env-var change (GROQ_VISION_MODEL / GROQ_GRADE_MODEL), not a code change.
VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")  # reads handwriting (needs image input)
GRADE_MODEL = os.getenv("GROQ_GRADE_MODEL", "openai/gpt-oss-120b")  # grades the transcript (text only)

MAX_IMAGES_PER_CALL = 3  # Groq vision models accept a small number of images per request

_client = None


def get_client():
    global _client
    if _client is None:
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set")
        _client = Groq(api_key=key)
    return _client


def _clean(text):
    """Remove <think> blocks and markdown bold so TOTAL: lines parse reliably."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    text = text.replace("**", "")
    return text


def _chat(model, messages, max_tokens, extra=None):
    """Call Groq with retries on rate limits. Optional params go in extra_body
    so it works on any groq SDK version; falls back if the model rejects them."""
    client = get_client()
    last = None
    for attempt in range(3):
        try:
            try:
                resp = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens,
                    extra_body=extra or None,
                )
            except BadRequestError:
                if not extra:
                    raise
                resp = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens,
                )
            return _clean(resp.choices[0].message.content)
        except RateLimitError as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise last


# ── Helpers to turn files into base64 JPEG pages ──────────────────────────────
def pdf_to_base64_images(pdf_path):
    doc = fitz.open(pdf_path)
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        images.append(base64.b64encode(pix.tobytes("jpeg")).decode("utf-8"))
    doc.close()
    return images


def image_to_base64(image_path):
    from PIL import Image
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((2000, 2000))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def extract_text_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    text = "".join(page.get_text() for page in doc)
    doc.close()
    return text


# ── Step 1: read handwriting (vision model, max 3 pages per call) ─────────────
def transcribe_pages(images_b64):
    parts = []
    for start in range(0, len(images_b64), MAX_IMAGES_PER_CALL):
        batch = images_b64[start:start + MAX_IMAGES_PER_CALL]
        content = [{
            "type": "text",
            "text": (
                "Transcribe this student's handwritten answer sheet exactly as written. "
                "Keep question numbers. Do not correct mistakes, do not grade, do not add comments. "
                "Write [illegible] for anything you cannot read. "
                f"These are pages {start + 1} to {start + len(batch)}."
            ),
        }]
        for i, b64 in enumerate(batch):
            content.append({"type": "text", "text": f"Page {start + i + 1}:"})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        text = _chat(
            VISION_MODEL,
            [{"role": "user", "content": content}],
            max_tokens=3000,
            extra={"reasoning_effort": "none"},
        )
        parts.append(text)
    return "\n\n".join(parts)


# ── Step 2: grade the transcript (text model) ─────────────────────────────────
def _fix_total(text, total_marks):
    """Recompute TOTAL from the per-question lines so the arithmetic is always right."""
    awarded, found = 0.0, False
    for m in re.finditer(r"^\s*Q\s*\d+[a-zA-Z()]*\s*:\s*([\d.]+)\s*/\s*([\d.]+)", text, re.M):
        awarded += float(m.group(1))
        found = True
    if not found:
        return text
    new_line = f"TOTAL: {awarded:g}/{total_marks}"
    if re.search(r"^\s*TOTAL:.*$", text, re.M):
        return re.sub(r"^\s*TOTAL:.*$", lambda _: new_line, text, count=1, flags=re.M)
    return text + "\n" + new_line


def grade_transcript(transcript, answer_key_text, total_marks):
    total_marks = total_marks or "the total shown in the answer key"
    prompt = f"""You are an expert teacher grading a student's answer sheet.

ANSWER KEY (questions, correct answers, marks):
{answer_key_text}

TOTAL MARKS: {total_marks}

STUDENT'S ANSWERS (transcribed from handwriting; [illegible] means unreadable):
{transcript}

Grade each question against the answer key. Give partial marks where the answer is partly correct.
If the answer key does not state marks per question, split the total marks sensibly across questions.
Reply in EXACTLY this format and nothing else:

Q1: [marks awarded]/[marks for Q1] — [short reason]
Q2: [marks awarded]/[marks for Q2] — [short reason]
(continue for all questions)
TOTAL: [total scored]/{total_marks}
OVERALL FEEDBACK: [2 lines of feedback]
"""
    result = _chat(GRADE_MODEL, [{"role": "user", "content": prompt}], max_tokens=4000)
    return _fix_total(result, total_marks)


def _grade_images(images_b64, answer_key_text, total_marks):
    transcript = transcribe_pages(images_b64)
    if not transcript.strip():
        raise RuntimeError("Could not read any text from the answer sheet")
    return grade_transcript(transcript, answer_key_text, total_marks)


# ── Public functions used by app.py ───────────────────────────────────────────
def grade_answer_pdf(student_pdf_path, answer_key_text, total_marks):
    return _grade_images(pdf_to_base64_images(student_pdf_path), answer_key_text, total_marks)


def grade_answer(image_path, answer_key, total_marks):
    """Grade an uploaded image (or PDF). Uses the same pipeline and output format
    as bulk grading, so results show correctly in Past Tests."""
    if image_path.lower().endswith(".pdf"):
        return grade_answer_pdf(image_path, answer_key, total_marks)
    return _grade_images([image_to_base64(image_path)], answer_key, total_marks)