import streamlit as st
import torch
import torchvision
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from PIL import Image
from torchvision.transforms import functional as F
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from pycocotools.coco import COCO
import boto3
import json
from dotenv import load_dotenv
import os
import time

# -----------------------------
# CONFIG
# -----------------------------
MODEL_PATH = r"C:\Users\fceva\OneDrive\Documentos\MBDS\Term 3\Deep Learning\CarDamage_Detection\02_Training\best_carDD_model.pth"
ANNOTATION_PATH = r"C:\Users\fceva\OneDrive\Documentos\MBDS\Term 3\Deep Learning\CarDamage_Detection\01_Data\annotations\instances_train2017.json"
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

st.set_page_config(
    page_title="AI Damage Assistant",
    page_icon="🚗",
    layout="wide"
)

# -----------------------------
# LOAD ENV
# -----------------------------
load_dotenv()

AWS_REGION = os.getenv("AWS_REGION")
BEDROCK_MODEL_ID = os.getenv("AWS_BEDROCK_PRIMARY_MODEL")

# Force .env credentials (avoid SSO issues)
bedrock = boto3.client(
    service_name="bedrock-runtime",
    region_name=AWS_REGION,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
)

# -----------------------------
# LOAD CNN MODEL
# -----------------------------
@st.cache_resource
def load_model():
    coco = COCO(ANNOTATION_PATH)
    num_classes = len(coco.getCatIds()) + 1

    model = torchvision.models.detection.fasterrcnn_resnet50_fpn(weights=None)

    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)

    model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))
    model.to(DEVICE)
    model.eval()

    cats = coco.loadCats(coco.getCatIds())
    id_to_name = {cat["id"]: cat["name"] for cat in cats}

    return model, id_to_name

model, id_to_name = load_model()

# -----------------------------
# SESSION STATE
# -----------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

if "damage_report" not in st.session_state:
    st.session_state.damage_report = None

# -----------------------------
# LLM FUNCTIONS
# -----------------------------
def call_bedrock(system_prompt, messages, max_tokens=1000):
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "system": system_prompt,
        "messages": messages
    }

    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(body)
    )

    response_body = json.loads(response["body"].read())
    return response_body["content"][0]["text"]


def get_initial_assessment(damage_report):

    system_prompt = """
You are a senior automotive damage assessor AI.

Based on structured CNN detections, provide:
- Executive summary
- Severity (Minor / Moderate / Severe)
- Detailed explanation of each damage
- Repair recommendation
- Estimated cost range (USD)
- Estimated repair time
- Insurance recommendation

Be structured and professional.
"""

    messages = [
        {
            "role": "user",
            "content": f"CNN Output JSON:\n{json.dumps(damage_report, indent=2)}"
        }
    ]

    return call_bedrock(system_prompt, messages)


def get_followup_response(damage_report, chat_history, user_prompt):

    system_prompt = """
You are an automotive damage expert AI assistant.

Use the prior CNN structured results and continue the conversation.
Remain consistent with previous cost estimates unless justified.
"""

    conversation = [
        {
            "role": "user",
            "content": f"Initial CNN Output:\n{json.dumps(damage_report, indent=2)}"
        }
    ]

    for msg in chat_history:
        conversation.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    conversation.append({
        "role": "user",
        "content": user_prompt
    })

    return call_bedrock(system_prompt, conversation, max_tokens=800)

# -----------------------------
# UI HEADER
# -----------------------------
st.title("🚗 AI Damage Assessment Assistant")
st.caption("Enterprise-grade AI system combining Computer Vision and LLM reasoning.")

# -----------------------------
# FILE UPLOAD
# -----------------------------
uploaded_file = st.file_uploader("Upload vehicle image", type=["jpg", "jpeg", "png"])

# -----------------------------
# IMAGE ANALYSIS
# -----------------------------
if uploaded_file:

    image = Image.open(uploaded_file).convert("RGB")
    st.image(image, width=400)

    img_tensor = F.to_tensor(image).to(DEVICE)

    with torch.no_grad():
        prediction = model([img_tensor])[0]

    threshold = 0.5
    detections = []

    for box, score, label in zip(
        prediction["boxes"],
        prediction["scores"],
        prediction["labels"]
    ):
        if score >= threshold:
            class_name = id_to_name.get(int(label), "Unknown")
            x1, y1, x2, y2 = box.cpu().numpy()
            area = float((x2 - x1) * (y2 - y1))

            detections.append({
                "type": class_name,
                "confidence": round(float(score), 3),
                "bounding_box_area": round(area, 2)
            })

    st.session_state.damage_report = detections

    # Draw bounding boxes
    fig, ax = plt.subplots(figsize=(6,5))
    ax.imshow(image)

    for d, box in zip(detections, prediction["boxes"]):
        if d["confidence"] >= threshold:
            x1, y1, x2, y2 = box.cpu().numpy()
            rect = patches.Rectangle(
                (x1, y1),
                x2 - x1,
                y2 - y1,
                linewidth=2,
                edgecolor='red',
                facecolor='none'
            )
            ax.add_patch(rect)
            ax.text(x1, y1, d["type"], color='yellow', fontsize=8)

    ax.axis("off")
    st.pyplot(fig)

    # -----------------------------
    # AUTO LLM REPORT
    # -----------------------------
    if detections:
        with st.spinner("Generating professional assessment..."):
            try:
                report = get_initial_assessment(detections)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": report
                })
                st.markdown(report)
            except Exception as e:
                st.error(f"LLM Error: {str(e)}")
    else:
        st.success("No significant damage detected.")

# -----------------------------
# FOLLOW-UP CHAT
# -----------------------------
if st.session_state.damage_report:

    prompt = st.chat_input("Ask follow-up questions about the damage...")

    if prompt:
        st.session_state.messages.append({
            "role": "user",
            "content": prompt
        })

        with st.spinner("Consulting AI damage expert..."):
            try:
                response = get_followup_response(
                    st.session_state.damage_report,
                    st.session_state.messages,
                    prompt
                )

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": response
                })

                st.markdown(response)

            except Exception as e:
                st.error(f"LLM Error: {str(e)}")