from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from authlib.integrations.flask_client import OAuth
from google import genai
from google.genai import types
import json, os, datetime

app = Flask(__name__)

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.secret_key = os.environ.get("SECRET_KEY", "local-dev-secret")

# ── Google OAuth setup ─────────────────────────────────
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"}
)
# ── Gemini setup ───────────────────────────────────────
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


# ── Mood journaling default personality ────────────────
DEFAULT_MOOD_PERSONALITY = """You are a compassionate mood journaling companion. Your role is to:
1. Listen and validate the user's feelings
2. Help them understand their emotions
3. Provide brief, actionable insights
4. Ask thoughtful follow-up questions
5. Be warm, non-judgmental, and supportive

Keep your responses SHORT (2-3 sentences max), crisp, and emotionally intelligent."""

# ── Mood-based response styling ─────────────────────────
MOOD_STYLES = {
    "happy": {"color": "#22c55e", "font_size": "16px", "emoji": "😊"},
    "sad": {"color": "#f87171", "font_size": "15px", "emoji": "🫂"},
    "anxious": {"color": "#f59e0b", "font_size": "14px", "emoji": "😰"},
    "angry": {"color": "#dc2626", "font_size": "15px", "emoji": "😤"},
    "calm": {"color": "#06b6d4", "font_size": "15px", "emoji": "😌"},
    "neutral": {"color": "#6366f1", "font_size": "14px", "emoji": "🤔"},
    "grateful": {"color": "#ec4899", "font_size": "16px", "emoji": "🙏"},
    "overwhelmed": {"color": "#8b5cf6", "font_size": "14px", "emoji": "😵"},
}

# ── Helper: current logged-in user id ─────────────────
def current_user_id():
    return session.get("user_id")

def current_user_name():
    return session.get("user_name", "User")

# ── Session file helpers ───────────────────────────────
def user_dir(user_id):
    path = os.path.join("chat_sessions", user_id)
    os.makedirs(path, exist_ok=True)
    return path

def session_file(user_id, name):
    return os.path.join(user_dir(user_id), f"{name}.json")

def load_session(user_id, name):
    path = session_file(user_id, name)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {
        "personality": DEFAULT_MOOD_PERSONALITY,
        "messages": [],
        "mood_log": []
    }

def detect_mood(text):
    """Detect mood from user text"""
    text_lower = text.lower()

    mood_keywords = {
        "happy": ["happy", "great", "excited", "amazing", "wonderful", "joy", "love"],
        "sad": ["sad", "unhappy", "depressed", "down", "crying", "lonely", "heartbroken"],
        "anxious": ["anxious", "nervous", "worried", "stressed", "panic", "afraid", "scared"],
        "angry": ["angry", "furious", "mad", "frustrated", "irritated", "annoyed", "hate"],
        "calm": ["calm", "peaceful", "relaxed", "serene", "meditate", "zen"],
        "grateful": ["grateful", "thankful", "blessed", "appreciate", "thanks"],
        "overwhelmed": ["overwhelmed", "stressed", "too much", "can't", "drowning"],
    }

    for mood, keywords in mood_keywords.items():
        if any(keyword in text_lower for keyword in keywords):
            return mood
    return "neutral"

def save_session(user_id, name, data):
    with open(session_file(user_id, name), "w") as f:
        json.dump(data, f)

def list_sessions(user_id):
    d = user_dir(user_id)
    return sorted([f.replace(".json", "") for f in os.listdir(d) if f.endswith(".json")])

# ── Auth decorator ─────────────────────────────────────
from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user_id():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ── Auth routes ────────────────────────────────────────
@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/auth/google")
def auth_google():
    redirect_uri = url_for("callback", _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route("/callback")
def callback():
    token = google.authorize_access_token()
    user_info = token.get("userinfo")
    session["user_id"] = user_info["sub"]       # unique Google user ID
    session["user_name"] = user_info["name"]
    session["user_email"] = user_info["email"]
    session["user_picture"] = user_info.get("picture", "")
    return redirect(url_for("home"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── Main app routes ────────────────────────────────────
@app.route("/")
@login_required
def home():
    return render_template("index.html",
        user_name=current_user_name(),
        user_email=session.get("user_email"),
        user_picture=session.get("user_picture")
    )

@app.route("/sessions", methods=["GET"])
@login_required
def get_sessions():
    return jsonify(list_sessions(current_user_id()))

@app.route("/sessions/create", methods=["POST"])
@login_required
def create_session():
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"error": "Session name is required"}), 400
    uid = current_user_id()
    if os.path.exists(session_file(uid, name)):
        return jsonify({"error": "Session already exists"}), 400
    save_session(uid, name, {
        "personality": DEFAULT_MOOD_PERSONALITY,
        "messages": [],
        "mood_log": []
    })
    return jsonify({"status": "created", "name": name})

@app.route("/sessions/<name>/history", methods=["GET"])
@login_required
def get_history(name):
    data = load_session(current_user_id(), name)
    simple = [
        {
            "role": "user" if m["role"] == "user" else "bot",
            "text": m["parts"][0]["text"],
            "time": m.get("time", ""),
            "mood": m.get("mood", "neutral"),
            "style": m.get("style", MOOD_STYLES["neutral"])
        }
        for m in data["messages"]
    ]
    mood_log = data.get("mood_log", [])
    return jsonify({"messages": simple, "personality": data["personality"], "mood_log": mood_log})

@app.route("/sessions/<name>/personality", methods=["POST"])
@login_required
def update_personality(name):
    personality = request.json.get("personality", "").strip()
    if not personality:
        return jsonify({"error": "Personality cannot be empty"}), 400
    uid = current_user_id()
    data = load_session(uid, name)
    data["personality"] = personality
    save_session(uid, name, data)
    return jsonify({"status": "updated"})

@app.route("/sessions/<name>/chat", methods=["POST"])
@login_required
def chat(name):
    user_message = request.json.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    uid = current_user_id()
    data = load_session(uid, name)
    now = datetime.datetime.now().strftime("%I:%M %p")

    # Detect user mood
    detected_mood = detect_mood(user_message)

    data["messages"].append({
        "role": "user",
        "parts": [{"text": user_message}],
        "time": now,
        "mood": detected_mood
    })

    # Log mood
    if "mood_log" not in data:
        data["mood_log"] = []
    data["mood_log"].append({"mood": detected_mood, "time": now})

    contents = [{"role": m["role"], "parts": m["parts"]} for m in data["messages"]]

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config={"system_instruction": data["personality"]}
    )

    reply = response.text

    # Get style for bot response (mirror user's mood)
    style = MOOD_STYLES.get(detected_mood, MOOD_STYLES["neutral"])

    data["messages"].append({
        "role": "model",
        "parts": [{"text": reply}],
        "time": now,
        "style": style
    })

    save_session(uid, name, data)
    return jsonify({"reply": reply, "time": now, "style": style, "mood": detected_mood})

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

@app.route("/sessions/<name>/describe-image", methods=["POST"])
@login_required
def describe_image(name):
    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400

    file = request.files["image"]
    if file.mimetype not in ALLOWED_IMAGE_TYPES:
        return jsonify({"error": "Unsupported image type"}), 400

    image_bytes = file.read()
    if len(image_bytes) > 10 * 1024 * 1024:  # 10 MB limit
        return jsonify({"error": "Image too large (max 10 MB)"}), 400

    caption = request.form.get("caption", "").strip()
    prompt = caption if caption else "Describe this image in detail."

    uid = current_user_id()
    data = load_session(uid, name)
    now = datetime.datetime.now().strftime("%I:%M %p")

    image_part = types.Part.from_bytes(data=image_bytes, mime_type=file.mimetype)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[image_part, prompt],
        config={"system_instruction": data["personality"]}
    )

    reply = response.text

    user_label = f"[Image] {caption}" if caption else "[Image uploaded for description]"
    data["messages"].append({"role": "user",  "parts": [{"text": user_label}], "time": now})
    data["messages"].append({"role": "model", "parts": [{"text": reply}],      "time": now})
    save_session(uid, name, data)

    return jsonify({"reply": reply, "time": now, "user_label": user_label})


@app.route("/sessions/<name>/delete", methods=["POST"])
@login_required
def delete_session(name):
    path = session_file(current_user_id(), name)
    if os.path.exists(path):
        os.remove(path)
    return jsonify({"status": "deleted"})

@app.route("/me")
@login_required
def me():
    return jsonify({
        "name": current_user_name(),
        "email": session.get("user_email"),
        "picture": session.get("user_picture")
    })


if __name__ == "__main__":
#    import webbrowser
#    webbrowser.open("http://127.0.0.1:5000")
    app.run(debug=False)