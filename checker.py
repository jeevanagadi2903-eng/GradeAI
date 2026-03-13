import os
import base64
import fitz  # pymupdf
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def pdf_to_base64_images(pdf_path):
    """Convert each page of PDF to base64 image"""
    doc = fitz.open(pdf_path)
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("jpeg")
        images.append(base64.b64encode(img_bytes).decode("utf-8"))
    doc.close()
    return images

def extract_text_from_pdf(pdf_path):
    """Extract text from PDF"""
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

def grade_answer_pdf(student_pdf_path, answer_key_text, total_marks):
    """Grade a student's PDF answer sheet against answer key"""

    # Convert student PDF pages to images
    student_images = pdf_to_base64_images(student_pdf_path)

    # Build content with all pages
    content = []

    # Add answer key as context
    content.append({
        "type": "text",
        "text": f"""You are an expert teacher grading a student's handwritten answer sheet.

ANSWER KEY (with questions and correct answers):
{answer_key_text}

TOTAL MARKS: {total_marks}

The following images are the student's answer sheet pages. 
Evaluate each question separately and provide:
1. Question number
2. Marks awarded out of question marks
3. Brief reason

At the end give the TOTAL SCORE.

Reply in EXACTLY this format:

Q1: [marks awarded]/[total marks for Q1] — [reason]
Q2: [marks awarded]/[total marks for Q2] — [reason]
(continue for all questions)

TOTAL: [total scored]/{total_marks}
OVERALL FEEDBACK: [2 lines of feedback]
"""
    })

    # Add all student answer sheet pages
    for i, img_b64 in enumerate(student_images):
        content.append({
            "type": "text",
            "text": f"Page {i+1} of student answer sheet:"
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
        })

    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{"role": "user", "content": content}],
        max_tokens=1500
    )

    return response.choices[0].message.content


def grade_answer(image_path, answer_key, total_marks):
    """Legacy function for image grading - kept for compatibility"""
    ext = image_path.split(".")[-1].lower()

    if ext == "pdf":
        return grade_answer_pdf(image_path, answer_key, total_marks)

    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    mime = "image/jpeg" if ext in ["jpg", "jpeg"] else "image/png"

    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_data}"}},
                {"type": "text", "text": f"""You are an expert teacher grading a student's handwritten answer.
ANSWER KEY: {answer_key}
TOTAL MARKS: {total_marks}
Read the handwriting, compare with answer key, give score and feedback.
Reply in this format:
STUDENT WROTE: (what you read)
SCORE: (number)/{total_marks}
FEEDBACK: (your feedback)"""}
            ]
        }],
        max_tokens=500
    )
    return response.choices[0].message.content