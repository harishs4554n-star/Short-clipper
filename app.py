import os, re, json, uuid, subprocess, threading
import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")
CLIPS_DIR = "/tmp/clips"
os.makedirs(CLIPS_DIR, exist_ok=True)
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
jobs = {}

def video_id_from_url(url):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None

def pick_clips(transcript_text):
    prompt = (
        "You are a short-form video editor. From this timestamped transcript, pick the 8 most "
        "engaging moments for YouTube Shorts. Each must be 20 to 55 seconds, start at the "
        "beginning of a thought and end on a complete thought, with a strong hook. "
        "Timestamps in the transcript may look like 1:02 or 1:02:30, so convert every time to "
        "total seconds. Return ONLY a JSON array of objects with keys start (seconds, integer), "
        "end (seconds, integer), title (short), reason (one sentence).\n\n" + transcript_text[:60000]
    )
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-1.5-flash:generateContent?key=" + GEMINI_KEY)
    r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}],
                                 "generationConfig": {"responseMimeType": "application/json"}}, timeout=120)
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)

@app.route("/")
def home():
    return send_from_directory("static", "index.html")

@app.route("/api/analyze", methods=["POST"])
def analyze():
    d = request.json
    vid = video_id_from_url(d.get("url", ""))
    text = d.get("transcript", "").strip()
    if not vid:
        return jsonify(error="That does not look like a YouTube link."), 400
    if len(text) < 200:
        return jsonify(error="Please paste the full transcript."), 400
    try:
        clips = pick_clips(text)
    except Exception as e:
        return jsonify(error="AI clip picking failed. Check the Gemini key."), 500
    return jsonify(video_id=vid, clips=clips)

def run_cut(job_id, vid, start, end):
    out = os.path.join(CLIPS_DIR, job_id + ".mp4")
    section = f"*{start}-{end}"
    raw = os.path.join(CLIPS_DIR, job_id + "_raw.mp4")
    try:
        subprocess.run(["yt-dlp", "-f", "bv*[height<=720]+ba/b[height<=720]",
                        "--download-sections", section, "--force-keyframes-at-cuts",
                        "--merge-output-format", "mp4", "-o", raw,
                        "https://www.youtube.com/watch?v=" + vid], check=True, timeout=500)
        vf = ("split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,boxblur=30:5[bg];[b]scale=1080:-2[fg];"
              "[bg][fg]overlay=(W-w)/2:(H-h)/2")
        subprocess.run(["ffmpeg", "-y", "-i", raw, "-vf", vf, "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "23", "-c:a", "aac", out],
                       check=True, timeout=500)
        jobs[job_id] = {"status": "done"}
    except Exception as e:
        jobs[job_id] = {"status": "error"}
    finally:
        if os.path.exists(raw):
            os.remove(raw)

@app.route("/api/cut", methods=["POST"])
def cut():
    d = request.json
    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "working"}
    threading.Thread(target=run_cut, args=(job_id, d["video_id"], int(d["start"]), int(d["end"]))).start()
    return jsonify(job_id=job_id)

@app.route("/api/status/<job_id>")
def status(job_id):
    return jsonify(jobs.get(job_id, {"status": "unknown"}))

@app.route("/clips/<path:name>")
def clip_file(name):
    return send_from_directory(CLIPS_DIR, name, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))