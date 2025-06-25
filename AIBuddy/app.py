from flask import Flask, request, jsonify, render_template, Response
from dotenv import load_dotenv
import os
import json
import requests
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, firestore
from io import StringIO
import csv
from firebase_admin import credentials, initialize_app

# === 初始化 ===
load_dotenv()
GPT_API_BASE = os.getenv("GPT_API_BASE")
GPT_API_KEY = os.getenv("GPT_API_KEY")

# Firebase 初始化
firebase_json = os.getenv("FIREBASE_KEY_JSON")
firebase_dict = json.loads(firebase_json)
firebase_dict["private_key"] = firebase_dict["private_key"].replace("\\n", "\n")
cred = credentials.Certificate(firebase_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Flask App
app = Flask(__name__, template_folder="templates")
base_dir = os.path.dirname(os.path.abspath(__file__))

# === 載入 prompt_05.txt ===
base_prompt = os.getenv("BASE_PROMPT", "尚未設定 BASE_PROMPT 環境變數")

# === 混淆矩陣判別主題 ===
def detect_topic(user_input):
    user_input = user_input.lower()
    if any(word in user_input for word in ["鄰居", "距離", "靠近", "knn", "分類靠誰"]):
        return "KNN"
    elif any(word in user_input for word in ["節點", "是非", "邏輯", "樹", "選擇題", "決策"]):
        return "決策樹"
    elif any(word in user_input for word in ["斜率", "上升", "趨勢", "變大變小", "線性", "數字變化"]):
        return "線性回歸"
    elif any(word in user_input for word in ["分開", "分界線", "感知器", "激勵函數", "線性分類"]):
        return "感知器"
    else:
        return None

# === 判斷是否為學生表達理解語句 ===
def expresses_understanding(msg):
    return any(phrase in msg for phrase in [
        "我懂了", "懂了", "我知道了", "我會了", "嗯嗯", "對", "了解", "ok", "好喔","喔喔",
        "原來是這樣", "我可以這樣想嗎", "所以是說", "好像懂了", "可以"
    ])

# === Chat 記憶體與出題狀態 ===
chat_history = {}
quiz_waiting = {}  # user_id: True 表示剛出完題，下一句是回答

# === 呼叫 GPT ===
def ask_gpt(messages):
    headers = {"Content-Type": "application/json"}
    if GPT_API_KEY:
        headers["Authorization"] = f"Bearer {GPT_API_KEY}" 
    data = {
        "model": "gpt-3.5-turbo",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 600
    }
    try:
        response = requests.post(GPT_API_BASE, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"⚠️ GPT 回應錯誤：{e}"

# ✅ GPT 自動判斷 GROW 階段
def classify_grow_stage(user_message):
    classification_prompt = [
        {"role": "system", "content": "請判斷以下句子屬於 GROW 模型的哪個階段，只回答 G、R、O 或 W，不要解釋。"},
        {"role": "user", "content": user_message}
    ]
    response = ask_gpt(classification_prompt)
    return response.strip().upper()

# === Firestore 備份 ===
def backup_to_firestore(user_id, role, content,current_grow_stage=None):
    try:
        db.collection("chat_logs").document(user_id).collection("messages").add({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc),
            "grow_stage": current_grow_stage
        })
    except Exception as e:
        print("❌ 備份失敗：", e)
