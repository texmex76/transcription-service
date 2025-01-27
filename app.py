import os
import toml
import datetime
import uuid
import subprocess
import shlex
import shutil

from flask import (
    Flask,
    request,
    render_template,
    send_from_directory,
    Response,
    redirect,
    url_for,
)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1000 * 1024 * 1024  # 1000 MB

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.toml")
BASE_DIR = None
WHISPER_BINARY = None
WHISPER_MODEL = None
FFMPEG_BINARY = None


def load_config():
    """Load TOML config and set global paths."""
    global BASE_DIR, WHISPER_BINARY, WHISPER_MODEL, FFMPEG_BINARY
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            config = toml.load(f)
            BASE_DIR = config.get("base_dir")
            WHISPER_BINARY = config.get("whisper_binary")
            WHISPER_MODEL = config.get("whisper_model")
            FFMPEG_BINARY = config.get("ffmpeg_binary")
    else:
        BASE_DIR = None
        WHISPER_BINARY = None
        WHISPER_MODEL = None
        FFMPEG_BINARY = None


# Load initial config at startup
load_config()


@app.route("/")
def index():
    """Render the main transcription page."""
    return render_template(
        "index.html", title="Transcribe", year=datetime.datetime.now().year
    )


@app.route("/upload", methods=["POST"])
def upload():
    """Handle file uploads."""
    if "audio_file" not in request.files:
        return "No file uploaded", 400

    f = request.files["audio_file"]
    if f.filename == "":
        return "No file selected", 400

    # Create a unique task ID and directory for the transcription job
    task_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + str(
        uuid.uuid4()
    )
    task_dir = os.path.join(BASE_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    # Save the uploaded file
    filename = secure_filename(f.filename)
    input_path = os.path.join(task_dir, filename)
    f.save(input_path)

    # Render the index again, but now with a task_id to trigger SSE
    return render_template(
        "index.html",
        title="Transcribe",
        year=datetime.datetime.now().year,
        task_id=task_id,
    )


@app.route("/progress/<task_id>")
def progress(task_id):
    """Stream transcription progress logs via SSE."""
    task_dir = os.path.join(BASE_DIR, task_id)
    if not os.path.isdir(task_dir):
        return "Invalid task ID", 404

    # Find our input file in the task directory
    input_path = None
    for f in os.listdir(task_dir):
        # Include all audio file endings you want to support
        if f.lower().endswith((".wav", ".mp3", ".webm", ".m4a", ".ogg")):
            input_path = os.path.join(task_dir, f)
            break

    if input_path is None:
        return "No input file found", 404

    def generate():
        # 1) Convert to 16kHz WAV
        yield "Converting audio to 16kHz...\n"
        out_wav = os.path.join(task_dir, "input.wav")
        ffmpeg_cmd = f"{FFMPEG_BINARY} -i {shlex.quote(input_path)} -ar 16000 {shlex.quote(out_wav)}"
        subprocess.call(shlex.split(ffmpeg_cmd))

        # 2) Run Whisper
        whisper_cmd = f"{WHISPER_BINARY} -otxt -osrt -m {WHISPER_MODEL} {shlex.quote(out_wav)}"
        yield "Running whisper...\n"

        log_file_path = os.path.join(task_dir, "stdout.log")
        with open(log_file_path, "w") as log_file:
            process = subprocess.Popen(
                shlex.split(whisper_cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in process.stdout:
                log_file.write(line)
                log_file.flush()
                yield f"data: {line.strip()}\n\n"
            process.wait()

        # 3) Rename generated txt/srt to output.txt / output.srt
        generated_txt = os.path.join(task_dir, "input.wav.txt")
        generated_srt = os.path.join(task_dir, "input.wav.srt")
        final_txt = os.path.join(task_dir, "output.txt")
        final_srt = os.path.join(task_dir, "output.srt")

        if os.path.exists(generated_txt):
            os.rename(generated_txt, final_txt)

            # Remove leading spaces from each line in the .txt file
            with open(final_txt, "r") as txt_file:
                lines = txt_file.readlines()

            with open(final_txt, "w") as txt_file:
                txt_file.writelines(line.lstrip() for line in lines)

        if os.path.exists(generated_srt):
            os.rename(generated_srt, final_srt)

        # Indicate we're done (so the client can redirect to /result/<task_id>)
        yield "data: DONE\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/result/<task_id>")
def result(task_id):
    """Show final result download links."""
    task_dir = os.path.join(BASE_DIR, task_id)
    if not os.path.isdir(task_dir):
        return "Invalid task ID", 404

    txt_path = os.path.join(task_dir, "output.txt")
    srt_path = os.path.join(task_dir, "output.srt")

    # If these files don't exist, it's likely still transcribing or an error
    if not (os.path.exists(txt_path) or os.path.exists(srt_path)):
        return "Transcription not found or not completed", 404

    # Render the same index, but now with a "completed" section
    return render_template(
        "index.html",
        title="Transcribe",
        year=datetime.datetime.now().year,
        result_task_id=task_id,
    )


@app.route("/download/<task_id>/<filename>")
def download_file(task_id, filename):
    """Download a specific file (e.g. output.txt, output.srt)."""
    task_dir = os.path.join(BASE_DIR, task_id)
    if not os.path.isfile(os.path.join(task_dir, filename)):
        return "File not found", 404
    return send_from_directory(task_dir, filename, as_attachment=True)


@app.route("/view/<task_id>/<filename>")
def view_file(task_id, filename):
    """Display a file in plain text (for the archive preview)."""
    task_dir = os.path.join(BASE_DIR, task_id)
    file_path = os.path.join(task_dir, filename)
    if not os.path.exists(file_path):
        return "File not found.", 404

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    return Response(content, mimetype="text/plain")


@app.route("/archive")
def archive():
    """Render the archive of transcriptions."""
    if not BASE_DIR or not os.path.isdir(BASE_DIR):
        return (
            "No transcriptions directory found. Please configure base_dir in settings.",
            500,
        )

    tasks = {}
    for d in os.listdir(BASE_DIR):
        task_dir = os.path.join(BASE_DIR, d)
        if os.path.isdir(task_dir):
            files = os.listdir(task_dir)
            tasks[d] = files

    return render_template(
        "archive.html",
        title="Archive",
        year=datetime.datetime.now().year,
        tasks=tasks,
    )


@app.route("/delete/<task_id>", methods=["POST"])
def delete_task(task_id):
    """Delete a transcription task directory."""
    task_dir = os.path.join(BASE_DIR, task_id)
    if os.path.exists(task_dir) and os.path.isdir(task_dir):
        shutil.rmtree(task_dir)
    return redirect(url_for("archive"))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Manage application settings (update config.toml)."""
    if request.method == "POST":
        config = {
            "base_dir": request.form["base_dir"],
            "whisper_binary": request.form["whisper_binary"],
            "whisper_model": request.form["whisper_model"],
            "ffmpeg_binary": request.form["ffmpeg_binary"],
        }
        with open(CONFIG_PATH, "w") as f:
            toml.dump(config, f)
        load_config()
        return redirect(url_for("settings"))

    # Load current config to populate the form
    config_data = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            config_data = toml.load(f)

    return render_template(
        "settings.html", title="Settings", config=config_data
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
