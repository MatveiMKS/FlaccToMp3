from flask import Flask, request, render_template, Response, jsonify
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import zipfile
import json
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import filedialog
import sys
import webbrowser
from tagger import tag_mp3

# When packaged with PyInstaller, bundled files are unpacked to sys._MEIPASS
FROZEN = getattr(sys, 'frozen', False)
BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

# Prefer an FFmpeg shipped alongside the app, fall back to the one on PATH
BUNDLED_FFMPEG = os.path.join(BASE_DIR, 'ffmpeg.exe')
FFMPEG = BUNDLED_FFMPEG if os.path.isfile(BUNDLED_FFMPEG) else shutil.which('ffmpeg')

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/browse')
def browse():
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    
    if request.args.get('type') == 'zip':
        folder_path = filedialog.askopenfilename(filetypes=[('ZIP archives', '*.zip')])
    else:
        folder_path = filedialog.askdirectory()
    root.destroy()
    
    if folder_path:
        folder_path = os.path.normpath(folder_path)
        
    return jsonify({'path': folder_path})

VALID_EXTENSIONS = ('.flac', '.wav')

def extract_zip_audio(zip_path, dest_dir):
    """Extracts every FLAC/WAV in the archive, however deeply nested, and returns their paths."""
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if not m.is_dir() and m.filename.lower().endswith(VALID_EXTENSIONS)]
        return [zf.extract(m, dest_dir) for m in members]

@app.route('/convert', methods=['POST'])
def convert():
    data = request.json
    input_dir = data.get('input_dir', '').strip()
    output_dir = data.get('output_dir', '').strip()
    create_folder = data.get('create_folder', False)
    tag = data.get('tag', False)

    def generate():
        # Holds the ZIP extraction folder, if any, so it is removed however the run ends
        temp_dirs = []
        try:
            yield from run(temp_dirs)
        finally:
            for temp_dir in temp_dirs:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def run(temp_dirs):
        # Validate Input Directory (or ZIP archive)
        is_zip = os.path.isfile(input_dir) and zipfile.is_zipfile(input_dir)
        if not input_dir or not (is_zip or os.path.isdir(input_dir)):
            yield f"data: {json.dumps({'status': 'error', 'msg': 'Input must be an existing folder or ZIP file.'})}\n\n"
            return

        # Normalize & Create Output Directory
        if create_folder:
            # "<input folder>_mp3", inside the chosen output folder (default: next to the input folder)
            norm_input_dir = os.path.normpath(os.path.abspath(input_dir))
            parent_dir = output_dir or os.path.dirname(norm_input_dir)
            input_name = os.path.basename(norm_input_dir)
            if is_zip:
                input_name = os.path.splitext(input_name)[0]
            norm_output_dir = os.path.normpath(os.path.join(parent_dir, input_name + '_mp3'))
        elif not output_dir:
            yield f"data: {json.dumps({'status': 'error', 'msg': 'Please specify an output directory.'})}\n\n"
            return
        else:
            norm_output_dir = os.path.normpath(output_dir)

        if not os.path.exists(norm_output_dir):
            if create_folder:
                try:
                    os.makedirs(norm_output_dir, exist_ok=True)
                except Exception as e:
                    yield f"data: {json.dumps({'status': 'error', 'msg': f'Failed to create output folder: {str(e)}'})}\n\n"
                    return
            else:
                yield f"data: {json.dumps({'status': 'error', 'msg': 'Output directory does not exist.'})}\n\n"
                return
        elif not os.path.isdir(norm_output_dir):
            yield f"data: {json.dumps({'status': 'error', 'msg': 'Target output path is a file, not a directory.'})}\n\n"
            return

        # Find both FLAC and WAV files
        if is_zip:
            yield f"data: {json.dumps({'status': 'info', 'msg': 'Extracting ZIP archive...'})}\n\n"
            temp_dirs.append(tempfile.mkdtemp(prefix='flac2mp3_'))
            try:
                audio_files = extract_zip_audio(input_dir, temp_dirs[0])
            except Exception as e:
                yield f"data: {json.dumps({'status': 'error', 'msg': f'Failed to extract ZIP: {str(e)}'})}\n\n"
                return
        else:
            audio_files = [os.path.join(input_dir, f) for f in os.listdir(input_dir) if f.lower().endswith(VALID_EXTENSIONS)]
        total_files = len(audio_files)

        if total_files == 0:
            yield f"data: {json.dumps({'status': 'error', 'msg': 'No FLAC or WAV files found in the input.'})}\n\n"
            return

        if not FFMPEG:
            yield f"data: {json.dumps({'status': 'error', 'msg': 'FFmpeg not found. Ensure it is installed and in your Windows PATH.'})}\n\n"
            return

        yield f"data: {json.dumps({'status': 'start', 'total': total_files})}\n\n"

        events = queue.Queue()
        cancelled = threading.Event()
        # SoundCloud lookups are network-bound; keep them few to avoid rate limiting
        tag_slots = threading.Semaphore(4)

        def process(in_path):
            if cancelled.is_set():
                return
            filename = os.path.basename(in_path)
            out_name = os.path.splitext(filename)[0] + ".mp3"
            out_path = os.path.join(norm_output_dir, out_name)

            # Base FFmpeg command
            cmd = [
                FFMPEG, '-y', '-i', in_path,
                '-ab', '320k', '-map_metadata', '0',
                '-id3v2_version', '3'
            ]

            # Preserve cover art stream if converting a FLAC file
            if filename.lower().endswith('.flac'):
                cmd.extend(['-map', '0:a', '-map', '0:v?', '-c:v', 'copy'])

            cmd.append(out_path)

            try:
                subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            except (OSError, subprocess.CalledProcessError):
                events.put({'status': 'warning', 'msg': f'Failed to convert {filename}'})
                return

            event = {'status': 'success', 'file': filename}
            if tag and not cancelled.is_set():
                try:
                    with tag_slots:
                        event['tag_msg'] = tag_mp3(out_path)
                except Exception as e:
                    event['tag_error'] = str(e)
            events.put(event)

        # One FFmpeg process per core; each worker converts a file, then tags it
        executor = ThreadPoolExecutor(max_workers=min(total_files, os.cpu_count() or 4))
        try:
            for in_path in audio_files:
                executor.submit(process, in_path)

            for i in range(1, total_files + 1):
                event = events.get()
                yield f"data: {json.dumps(event)}\n\n"
                yield f"data: {json.dumps({'status': 'progress', 'current': i, 'total': total_files, 'file': event.get('file', '')})}\n\n"
        finally:
            # Also reached when the browser disconnects mid-run
            cancelled.set()
            # Wait for in-flight files so extracted ZIP contents are no longer in use
            executor.shutdown(wait=True, cancel_futures=True)

        yield f"data: {json.dumps({'status': 'done'})}\n\n"

    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    print("Starting Web Server. Open http://127.0.0.1:5000 in your browser.")
    if FROZEN:
        # Packaged app: no debug reloader, and open the page for the user
        print("Close this window to quit.")
        threading.Timer(1, webbrowser.open, args=("http://127.0.0.1:5000",)).start()
        app.run(port=5000)
    else:
        app.run(debug=True, port=5000)