from flask import Flask, request, jsonify, render_template, Response
from dotenv import load_dotenv
import os
import json
import requests
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, firestore
from firebase_admin import initialize_app
from io import StringIO
import csv

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
def backup_to_firestore(user_id, role, content, current_grow_stage=None):
    try:
        print(f"📝 寫入前內容：{user_id=}, {role=}, {content[:20]=}, {current_grow_stage=}")
        db.collection("chat_logs").document(user_id).collection("messages").add({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc),
            "grow_stage": current_grow_stage
        })
        print("✅ Firestore 寫入成功")
    except Exception as e:
        print("❌ 備份失敗：", e)
        import traceback
        traceback.print_exc()

# === 首頁 ===
@app.route("/")
def index():
    return render_template("index.html")

# === Chat 端點 ===
@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_id = data.get("user_id", "web_user")
    user_msg = data.get("message", "").strip()

    if user_id not in chat_history:
        chat_history[user_id] = [{"role": "system", "content": base_prompt}]
        quiz_waiting[user_id] = False

    # ✅ 若偵測到學生問「怎麼算」「公式」「等於」等，插入強制提醒
    if any(word in user_msg for word in ["怎麼算", "公式", "等於", "算式", "=","怎麼求","求法"]):
        chat_history[user_id].insert(1, {
            "role": "system",
            "content": "請記住：你不能直接給公式、定義或標準答案，請用生活化情境的具體數字問題與觀察問題，幫助學生一步步發現規律！每次說明後，請加入一句數值引導式反問句，例如：『你覺得每⋯會增加多少呢？可以怎麼想？』"
        })

    chat_history[user_id].append({"role": "user", "content": user_msg})

    def is_greeting_by_gpt(message):
        prompt = [
            {"role": "system", "content": "請判斷以下句子是否是打招呼語，例如 '你好'、'hi'、'哈囉' 等等，如果是請只回答 True，如果不是請只回答 False。"},
            {"role": "user", "content": message}
        ]
        response = ask_gpt(prompt)
        return response.strip().lower() == "true"

    current_grow_stage = classify_grow_stage(user_msg)
    backup_to_firestore(user_id, "user", user_msg, current_grow_stage)

    if len(chat_history[user_id]) == 2 and is_greeting_by_gpt(user_msg):
        welcome = "你好呀！😊 今天你想一起學什麼主題呢？是感知器、多元決策樹、線性迴歸、K-近鄰演算法，還是貝氏分類器呢？你是想先了解概念？還是想看看例題怎麼解？還是其他的？你說說看～ ✨"
        chat_history[user_id].append({"role": "assistant", "content": welcome})
        backup_to_firestore(user_id, "assistant", welcome, current_grow_stage)
        return jsonify({"reply": welcome})

    if expresses_understanding(user_msg):
        quiz_waiting[user_id] = True
        chat_history[user_id].append({
            "role": "user",
            "content": "請根據剛才的主題出一題生活化且適合國中一年級生的題目讓我驗證理解，不需要再解釋剛才的內容。"
        })

    elif quiz_waiting.get(user_id, False):
        quiz_waiting[user_id] = False
        chat_history[user_id].append({
            "role": "user",
            "content": "請判斷我剛剛的回答是否正確，用自然語氣講出對或錯的理由，如果回答錯誤請補充生活例子解釋，並出一題類似的新題目讓我再試一次。"
        })

    ai_reply = ask_gpt(chat_history[user_id])
    chat_history[user_id].append({"role": "assistant", "content": ai_reply})
    backup_to_firestore(user_id, "assistant", ai_reply, current_grow_stage)

    return jsonify({"reply": ai_reply})

# === 查詢 logs ===
@app.route("/logs", methods=["GET"])
def get_logs():
    user_id = request.args.get("user_id", "web_user")
    output_format = request.args.get("format", "json")

    try:
        docs = db.collection("chat_logs").document(user_id).collection("messages").order_by("timestamp").stream()
        logs = [doc.to_dict() for doc in docs]

        if output_format == "csv":
            si = StringIO()
            cw = csv.DictWriter(si, fieldnames=["timestamp", "role", "content"])
            cw.writeheader()
            for row in logs:
                cw.writerow(row)
            output = si.getvalue()
            return Response(output, mimetype="text/csv",
                            headers={"Content-Disposition": f"attachment;filename={user_id}_chatlog.csv"})
        else:
            return jsonify(logs)

    except Exception as e:
        return jsonify({"error": str(e)})

# === 啟動伺服器 ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
