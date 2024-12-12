import os
import datetime
import uuid
import subprocess
from flask import (
    Flask,
    request,
    render_template_string,
    send_from_directory,
    Response,
    redirect,
    url_for
)
from werkzeug.utils import secure_filename
import shlex
import shutil

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1000 * 1024 * 1024  # 1000 MB

# Configuration constants
BASE_DIR = "/home/gstrein/transcriptions"
WHISPER_BINARY = "/home/gstrein/Documents/repos/whisper.cpp-1.5.1/build/bin/main"
WHISPER_MODEL = "/home/gstrein/models/whisper/ggml-large-v1.bin"
FFMPEG_BINARY = "ffmpeg"

if not os.path.exists(BASE_DIR):
    os.makedirs(BASE_DIR)

TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Audio Transcription</title>
<style>
body {
    font-family: Arial, sans-serif;
    background: #fafafa;
    color: #333;
    margin: 0; padding: 0;
}
header, footer {
    background: #FF9800;
    color: #fff;
    padding: 20px;
    text-align: center;
}
.container {
    max-width: 800px;
    margin: auto;
    padding: 20px;
}
h1 {
    color: #fff;
}
h2 {
    color: #FF9800;
}
.form-section, .record-section {
    background: #fff;
    border: 1px solid #ddd;
    padding: 20px;
    margin-bottom: 20px;
    border-radius: 5px;
}
.button {
    background: #FF9800;
    color: #fff;
    border: none;
    padding: 10px 20px;
    cursor: pointer;
    border-radius: 5px;
}
.button:hover {
    background: #E68A00;
}
.record-controls {
    display: flex;
    gap: 10px;
    margin-bottom: 10px;
    align-items: center;
}
#stdoutput {
    background: #f0f0f0;
    border: 1px solid #ccc;
    padding: 10px;
    height: 300px;
    overflow-y: auto;
    font-family: monospace;
    white-space: pre-wrap;
    word-wrap: break-word;
}
.download-links {
    margin-top: 20px;
}
.download-links a {
    margin-right: 20px;
    text-decoration: none;
    color: #FF9800;
    font-weight: bold;
}
.archive-link {
    display: inline-block;
    margin-bottom: 20px;
    text-decoration: none;
    background: #FF9800;
    color: #fff;
    padding: 10px;
    border-radius: 5px;
}
.archive-link:hover {
    background: #E68A00;
}
</style>
</head>
<body>

<header>
    <h1>Audio Transcription Service</h1>
</header>

<div class="container">

    <a class="archive-link" href="/archive">View Archive</a>

    <div class="form-section">
        <h2>Upload Audio</h2>
        <form method="POST" action="/upload" enctype="multipart/form-data" id="uploadForm">
            <p>Select an audio file to transcribe (WAV recommended, otherwise it will be converted):</p>
            <input type="file" name="audio_file" accept="audio/*" required />
            <br><br>
            <button class="button" type="submit">Upload and Transcribe</button>
        </form>
    </div>

    <div class="record-section">
        <h2>Record Audio</h2>
        <p>Record audio using your microphone and then submit for transcription:</p>
        <div class="record-controls">
            <button class="button" id="recordBtn">Start Recording</button>
            <button class="button" id="stopBtn" disabled>Stop Recording</button>
            <span>Recording Time: <span id="recordingTime">0</span> s</span>
        </div>
        <audio id="playback" controls style="display:none; margin-bottom:10px;"></audio>
        <button class="button" id="submitRecordingBtn" style="display:none;">Submit Recording for Transcription</button>
    </div>

    {% if task_id %}
    <h2>Transcription in Progress</h2>
    <div id="stdoutput">Waiting for output...</div>
    <script>
    var eventSource = new EventSource("/progress/{{task_id}}");
    var stdoutput = document.getElementById("stdoutput");

    eventSource.onmessage = function(e) {
        if (e.data.startsWith("DONE")) {
            eventSource.close();
            window.location.href = "/result/{{task_id}}";
        } else {
            stdoutput.textContent += e.data + "\n";
            stdoutput.scrollTop = stdoutput.scrollHeight;
        }
    };
    </script>
    {% endif %}

    {% if result_task_id %}
    <h2>Transcription Completed</h2>
    <div class="download-links">
        <a href="/download/{{result_task_id}}/output.txt" target="_blank">Download TXT</a>
        <a href="/download/{{result_task_id}}/output.srt" target="_blank">Download SRT</a>
        <a href="/download/{{result_task_id}}/stdout.log" target="_blank">View Stdout</a>
    </div>
    {% endif %}

</div>

<footer>
    <p>&copy; {{year}} Audio Transcription Service</p>
</footer>

<script>
let mediaRecorder;
let recordedChunks = [];
let recordingStartTime = null;
let recordingInterval = null;

const recordBtn = document.getElementById('recordBtn');
const stopBtn = document.getElementById('stopBtn');
const playback = document.getElementById('playback');
const submitRecordingBtn = document.getElementById('submitRecordingBtn');
const recordingTimeElem = document.getElementById('recordingTime');

recordBtn.addEventListener('click', async () => {
    recordedChunks = [];
    let stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.start();

    recordBtn.disabled = true;
    stopBtn.disabled = false;

    recordingStartTime = Date.now();
    recordingInterval = setInterval(() => {
        let elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
        recordingTimeElem.textContent = elapsed;
    }, 1000);

    mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
            recordedChunks.push(e.data);
        }
    };
});

stopBtn.addEventListener('click', () => {
    mediaRecorder.stop();

    stopBtn.disabled = true;
    recordBtn.disabled = false;

    clearInterval(recordingInterval);

    mediaRecorder.onstop = (e) => {
        let blob = new Blob(recordedChunks, { type: 'audio/webm' });
        let url = URL.createObjectURL(blob);
        playback.src = url;
        playback.style.display = 'block';
        submitRecordingBtn.style.display = 'inline-block';

        submitRecordingBtn.onclick = async () => {
            const formData = new FormData();
            formData.append("audio_file", blob, "recording.webm");

            let response = await fetch("/upload", {
                method: "POST",
                body: formData
            });

            if (response.ok) {
                let text = await response.text();
                document.open();
                document.write(text);
                document.close();
            } else {
                alert("Error submitting recording.");
            }
        };
    };
});
</script>

</body>
</html>
"""

# Updated archive template
ARCHIVE_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Archive - Past Transcriptions</title>
<style>
body {
    font-family: Arial, sans-serif;
    background: #fafafa;
    color: #333;
    margin: 0; padding: 0;
}
header, footer {
    background: #FF9800;
    color: #fff;
    padding: 20px;
    text-align: center;
}
h1 {
    color: #fff;
}

/* Use the entire width */
.container {
    display: flex;
    width: 100%;
    min-height: 90vh;
    box-sizing: border-box;
}
.left-panel {
    width: 500px;
    background: #fafafa;
    padding: 20px;
    box-sizing: border-box;
}
.right-panel-container {
    flex: 1;
    display: flex;
    flex-direction: column;
    padding: 20px;
    box-sizing: border-box;
}
.task-list {
    background: #fff;
    border: 1px solid #ddd;
    padding: 20px;
    border-radius: 5px;
}
.task {
    border-bottom: 1px solid #ddd;
    padding: 10px 0;
}
.task:last-child {
    border-bottom: none;
}
.task h2 {
    margin: 0 0 10px 0;
    color: #FF9800;
}
.download-links a {
    margin-right: 20px;
    text-decoration: none;
    color: #FF9800;
    font-weight: bold;
    cursor: pointer;
}

.button {
    background: #FF9800;
    color: #fff;
    border: none;
    padding: 5px 10px;
    cursor: pointer;
    border-radius: 5px;
}
.button:hover {
    background: #E68A00;
}

/* The preview area */
.preview-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
}

#file-content {
    background: #fff;
    border: 1px solid #ddd;
    border-radius: 5px;
    padding: 10px;
    white-space: pre-wrap;
    word-wrap: break-word;
    overflow: auto;
    flex: 1;
    font-family: monospace;
}

</style>
</head>
<body>

<header>
    <h1>Archive - Past Transcriptions</h1>
</header>

<div class="container">
    <div class="left-panel">
        <p><a href="/" style="text-decoration:none; color:#FF9800; font-weight:bold;">Back to Home</a></p>
        <div class="task-list">
            {% if tasks %}
                {% for task in tasks %}
                <div class="task">
                    <h2>{{ task }}</h2>
                    <div class="download-links">
                        {% if 'output.txt' in tasks[task] %}
                        <a href="#" onclick="showFile('{{task}}','output.txt')">TXT</a>
                        {% endif %}
                        {% if 'output.srt' in tasks[task] %}
                        <a href="#" onclick="showFile('{{task}}','output.srt')">SRT</a>
                        {% endif %}
                        {% if 'stdout.log' in tasks[task] %}
                        <a href="#" onclick="showFile('{{task}}','stdout.log')">Stdout</a>
                        {% endif %}
                    </div>
                    <form method="POST" action="/delete/{{task}}" style="margin-top:10px;">
                        <button class="button" type="submit">Delete</button>
                    </form>
                </div>
                {% endfor %}
            {% else %}
                <p>No archived transcriptions found.</p>
            {% endif %}
        </div>
    </div>

    <div class="right-panel-container">
        <div class="preview-header">
            <span id="file-name-display">Select a file to view.</span>
            <button id="download-button" class="button" style="display:none;">Download</button>
        </div>
        <div id="file-content">
            Select a file on the left to view its contents here.
        </div>
    </div>
</div>

<footer>
    <p>&copy; {{year}} Audio Transcription Service</p>
</footer>

<script>
let currentTaskId = null;
let currentFileName = null;

async function showFile(task_id, filename) {
    const contentDiv = document.getElementById('file-content');
    const fileNameDisplay = document.getElementById('file-name-display');
    const downloadButton = document.getElementById('download-button');

    currentTaskId = task_id;
    currentFileName = filename;

    fileNameDisplay.textContent = `Viewing: ${filename}`;
    downloadButton.style.display = 'inline-block';
    downloadButton.onclick = () => {
        window.location.href = `/download/${task_id}/${filename}`;
    };

    contentDiv.textContent = "Loading...";
    try {
        let response = await fetch(`/view/${task_id}/${filename}`);
        if (response.ok) {
            let text = await response.text();
            // Wrap in a <pre> for preserving whitespace and newlines
            contentDiv.innerHTML = "<pre>" + escapeHTML(text) + "</pre>";
        } else {
            contentDiv.textContent = "Failed to load file.";
        }
    } catch (e) {
        contentDiv.textContent = "Error loading file.";
    }
}

function escapeHTML(str) {
    return str.replace(/[&<>"'`=\/]/g, function (s) {
      var entityMap = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
        '/': '&#x2F;'
      };
      return entityMap[s];
    });
}
</script>

</body>
</html>
"""

@app.route("/", methods=["GET"])
def index():
    return render_template_string(TEMPLATE, year=datetime.datetime.now().year)


@app.route("/upload", methods=["POST"])
def upload():
    if "audio_file" not in request.files:
        return "No file uploaded", 400
    f = request.files["audio_file"]
    if f.filename == "":
        return "No file selected", 400

    filename = secure_filename(f.filename)
    task_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + str(uuid.uuid4())
    task_dir = os.path.join(BASE_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    input_path = os.path.join(task_dir, filename)
    f.save(input_path)

    return run_transcription(task_id, input_path)


def run_transcription(task_id, input_path):
    return render_template_string(
        TEMPLATE, year=datetime.datetime.now().year, task_id=task_id
    )


@app.route("/progress/<task_id>")
def progress(task_id):
    task_dir = os.path.join(BASE_DIR, task_id)
    input_path = None
    for f in os.listdir(task_dir):
        if f.lower().endswith((".wav", ".mp3", ".webm", ".m4a", ".ogg")):
            input_path = os.path.join(task_dir, f)
            break

    if input_path is None:
        return "No input file found", 404

    def generate():
        out_wav = os.path.join(task_dir, "input.wav")
        ffmpeg_cmd = f"{FFMPEG_BINARY} -i {shlex.quote(input_path)} -ar 16000 {shlex.quote(out_wav)}"
        yield "Converting audio to 16kHz...\n"
        subprocess.call(shlex.split(ffmpeg_cmd))

        whisper_cmd = f"{WHISPER_BINARY} -otxt -osrt -m {WHISPER_MODEL} {shlex.quote(out_wav)}"
        yield "Running whisper...\n"

        with open(os.path.join(task_dir, "stdout.log"), "w") as log_file:
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

        generated_txt = os.path.join(task_dir, "input.wav.txt")
        generated_srt = os.path.join(task_dir, "input.wav.srt")
        final_txt = os.path.join(task_dir, "output.txt")
        final_srt = os.path.join(task_dir, "output.srt")

        if os.path.exists(generated_txt):
            os.rename(generated_txt, final_txt)
        if os.path.exists(generated_srt):
            os.rename(generated_srt, final_srt)

        yield "data: DONE\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/result/<task_id>")
def result(task_id):
    task_dir = os.path.join(BASE_DIR, task_id)
    txt_path = os.path.join(task_dir, "output.txt")
    srt_path = os.path.join(task_dir, "output.srt")
    if not os.path.isfile(txt_path) or not os.path.isfile(srt_path):
        return "Transcription not found or not completed", 404

    return render_template_string(
        TEMPLATE, year=datetime.datetime.now().year, result_task_id=task_id
    )


@app.route("/download/<task_id>/<filename>")
def download_file(task_id, filename):
    task_dir = os.path.join(BASE_DIR, task_id)
    return send_from_directory(task_dir, filename, as_attachment=True)


@app.route("/view/<task_id>/<filename>")
def view_file(task_id, filename):
    task_dir = os.path.join(BASE_DIR, task_id)
    file_path = os.path.join(task_dir, filename)
    if not os.path.exists(file_path):
        return "File not found.", 404

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    return Response(content, mimetype="text/plain")


@app.route("/archive")
def archive():
    tasks = {}
    for d in os.listdir(BASE_DIR):
        task_dir = os.path.join(BASE_DIR, d)
        if os.path.isdir(task_dir):
            files = os.listdir(task_dir)
            tasks[d] = files

    return render_template_string(
        ARCHIVE_TEMPLATE, year=datetime.datetime.now().year, tasks=tasks
    )

@app.route("/delete/<task_id>", methods=["POST"])
def delete_task(task_id):
    task_dir = os.path.join(BASE_DIR, task_id)
    if os.path.exists(task_dir) and os.path.isdir(task_dir):
        shutil.rmtree(task_dir)
    return redirect(url_for('archive'))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)

