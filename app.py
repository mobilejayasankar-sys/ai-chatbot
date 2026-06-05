from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from authlib.integrations.flask_client import OAuth
from google import genai
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
        "personality": "You are a helpful and friendly assistant.",
        "messages": []
    }

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
        "personality": "You are a helpful and friendly assistant.",
        "messages": []
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
            "time": m.get("time", "")
        }
        for m in data["messages"]
    ]
    return jsonify({"messages": simple, "personality": data["personality"]})

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

    data["messages"].append({
        "role": "user",
        "parts": [{"text": user_message}],
        "time": now
    })

    contents = [{"role": m["role"], "parts": m["parts"]} for m in data["messages"]]

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config={"system_instruction": data["personality"]}
    )

    reply = response.text
    data["messages"].append({
        "role": "model",
        "parts": [{"text": reply}],
        "time": now
    })

    save_session(uid, name, data)
    return jsonify({"reply": reply, "time": now})

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