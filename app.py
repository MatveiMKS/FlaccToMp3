from flask import Flask, request, render_template, Response, jsonify
import os
import queue
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import zipfile
import json
from concurrent.futures import ThreadPoolExecutor
import sys
import webbrowser
from tagger import tag_file, describe_tag_result, copy_tags_to_wav, bits_per_sample, TAGGABLE_EXTENSIONS

# When packaged with PyInstaller, bundled files are unpacked to sys._MEIPASS
FROZEN = getattr(sys, 'frozen', False)
WINDOWS = os.name == 'nt'
MAC = sys.platform == 'darwin'
BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

# The packaged app has no console: give libraries that print something to write to
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

# Prefer an FFmpeg shipped alongside the app, fall back to the one on PATH
BUNDLED_FFMPEG = os.path.join(BASE_DIR, 'ffmpeg.exe' if WINDOWS else 'ffmpeg')
FFMPEG = BUNDLED_FFMPEG if os.path.isfile(BUNDLED_FFMPEG) else shutil.which('ffmpeg')
# Keeps FFmpeg from flashing a console window when the app itself has none
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

APP_NAME = 'Sound Converter'
# Window and taskbar icon; the packaged exe and app bundle embed their own (see the build commands)
ICON = os.path.join(BASE_DIR, 'static', 'icon.ico') if WINDOWS else None
# macOS keeps port 5000 for its AirPlay Receiver
DEFAULT_PORT = 5050 if MAC else 5000

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'), static_folder=os.path.join(BASE_DIR, 'static'))

# The native window, when the app runs in one (None in --browser mode)
window = None

# Everything FFmpeg can decode that is worth turning into an MP3 or WAV
AUDIO_EXTENSIONS = (
    '.flac', '.wav', '.aiff', '.aif', '.aifc', '.m4a', '.m4b', '.aac', '.alac', '.ogg', '.oga', '.opus',
    '.wma', '.ape', '.wv', '.tta', '.mka', '.mpc', '.ac3', '.dts', '.caf', '.au', '.amr', '.mp2',
    '.dsf', '.dff', '.w64', '.spx', '.tak', '.3gp'
)
OUTPUT_FORMATS = ('mp3', 'wav')

class Job:
    """One running conversion or tagging run, so it can be cancelled from another request."""

    def __init__(self):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.procs = set()

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            for proc in self.procs:
                proc.kill()

    def run(self, cmd):
        """Runs a command to completion and returns (exit code, stderr text)."""
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        with self.lock:
            self.procs.add(proc)
        try:
            # cancel() may have run just before the process was registered
            if self.cancelled.is_set():
                proc.kill()
            _, stderr = proc.communicate()
        finally:
            with self.lock:
                self.procs.discard(proc)
        return proc.returncode, stderr.decode('utf-8', errors='replace')

JOBS = {}

def sse(**event):
    return f"data: {json.dumps(event)}\n\n"

@app.route('/')
def index():
    return render_template('index.html', extensions=AUDIO_EXTENSIONS + TAGGABLE_EXTENSIONS + ('.zip',))

def mac_dialog(patterns):
    """Native macOS picker for browser mode: tkinter only works on the main thread there."""
    if patterns:
        types = ', '.join(f'"{pattern[2:]}"' for pattern in patterns)
        script = f'POSIX path of (choose file of type {{{types}}})'
    else:
        script = 'POSIX path of (choose folder)'
    # Cancelling makes osascript fail with nothing on stdout
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    return result.stdout.strip()

@app.route('/browse')
def browse():
    kind = request.args.get('type')
    file_types = {
        # pywebview only accepts letters, digits and spaces in the description
        'file': ('Audio and ZIP', ['*.zip'] + ['*' + ext for ext in AUDIO_EXTENSIONS]),
        'tag': ('MP3 and WAV', ['*' + ext for ext in TAGGABLE_EXTENSIONS])
    }.get(kind)

    if window:
        import webview
        if file_types:
            picked = window.create_file_dialog(webview.FileDialog.OPEN, file_types=(f"{file_types[0]} ({';'.join(file_types[1])})",))
        else:
            picked = window.create_file_dialog(webview.FileDialog.FOLDER)
        path = picked[0] if picked else ''
    elif MAC:
        path = mac_dialog(file_types[1] if file_types else None)
    else:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        if file_types:
            path = filedialog.askopenfilename(filetypes=[(file_types[0], ' '.join(file_types[1]))])
        else:
            path = filedialog.askdirectory()
        root.destroy()

    if path:
        path = os.path.normpath(path)

    return jsonify({'path': path})

@app.route('/cancel', methods=['POST'])
def cancel():
    job = JOBS.get((request.json or {}).get('job'))
    if job:
        job.cancel()
    return jsonify({'ok': bool(job)})

@app.route('/open', methods=['POST'])
def open_folder():
    path = (request.json or {}).get('path', '')
    ok = os.path.isdir(path)
    if ok:
        if WINDOWS:
            os.startfile(path)
        else:
            subprocess.Popen(['open' if MAC else 'xdg-open', path])
    return jsonify({'ok': ok})

def find_sources(source, extensions, temp_dirs, allow_zip=True):
    """Lists the audio in a folder, a ZIP (however deeply nested) or a single file.

    Returns sorted (path, id) pairs, where id names the file within the source, or None if the source is invalid.
    A ZIP is extracted to a temp folder, which is appended to temp_dirs.
    """
    if os.path.isdir(source):
        found = [(os.path.join(source, f), f) for f in os.listdir(source) if f.lower().endswith(extensions)]
    elif not os.path.isfile(source):
        return None
    elif source.lower().endswith(extensions):
        found = [(source, os.path.basename(source))]
    elif allow_zip and zipfile.is_zipfile(source):
        temp_dirs.append(tempfile.mkdtemp(prefix='flac2mp3_'))
        with zipfile.ZipFile(source) as zf:
            members = [m for m in zf.infolist() if not m.is_dir() and m.filename.lower().endswith(extensions)]
            found = [(zf.extract(m, temp_dirs[-1]), m.filename) for m in members]
    else:
        return None
    return sorted(found, key=lambda item: item[1].lower())

def unique_names(names):
    """Appends _2, _3... to names already taken (ignoring case), so no two outputs share a file."""
    taken = set()
    result = []
    for name in names:
        candidate, number = name, 2
        while candidate.lower() in taken:
            candidate = f"{name}_{number}"
            number += 1
        taken.add(candidate.lower())
        result.append(candidate)
    return result

def error_line(text):
    """The first thing FFmpeg complained about: later lines are only consequences of it."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # Drop the "[flac @ 000001f3...] " prefix naming the component
    return re.sub(r'^(\[[^\]]*\]\s*)+', '', lines[0])[:300] if lines else ''

def stream_run(prepare):
    """Wraps a run in an event stream, registering it for /cancel and cleaning up however it ends.

    prepare(job, temp_dirs) yields the events.
    """
    job_id = (request.json or {}).get('job')

    def generate():
        job = Job()
        if job_id:
            JOBS[job_id] = job
        # Holds the ZIP extraction folder, if any
        temp_dirs = []
        try:
            yield from prepare(job, temp_dirs)
        finally:
            # Also reached when the browser disconnects mid-run
            job.cancel()
            for temp_dir in temp_dirs:
                shutil.rmtree(temp_dir, ignore_errors=True)
            JOBS.pop(job_id, None)

    return Response(generate(), mimetype='text/event-stream')

def work(job, items, worker, max_workers):
    """Runs worker(item) -> event over a thread pool, yielding each event plus a progress event."""
    events = queue.Queue()
    total = len(items)

    def process(item):
        if job.cancelled.is_set():
            events.put(None)
            return
        try:
            events.put(worker(item))
        except Exception as e:
            events.put({'status': 'warning', 'code': 'failed', 'detail': str(e), 'msg': f'Failed: {e}'})

    executor = ThreadPoolExecutor(max_workers=max(1, min(total, max_workers)))
    try:
        for item in items:
            executor.submit(process, item)

        done = 0
        finished = False
        for _ in range(total):
            event = events.get()
            # None: the file was skipped or interrupted by a cancel
            if event is None:
                continue
            done += 1
            yield sse(**event)
            yield sse(status='progress', current=done, total=total, file=event.get('file', ''))
        finished = True
    finally:
        # Reached early when the browser disconnects mid-run: stop the remaining files
        if not finished:
            job.cancel()
        # Wait for in-flight files so extracted ZIP contents are no longer in use
        executor.shutdown(wait=True, cancel_futures=True)

@app.route('/convert', methods=['POST'])
def convert():
    data = request.json
    input_dir = data.get('input_dir', '').strip()
    output_dir = data.get('output_dir', '').strip()
    create_folder = data.get('create_folder', False)
    tag = data.get('tag', False)
    fmt = data.get('format') if data.get('format') in OUTPUT_FORMATS else 'mp3'
    # Retry: only these source ids are converted again
    only = data.get('only')

    # Events carry a 'code' the page translates; 'msg' is the English fallback
    def run(job, temp_dirs):
        # Validate Input (folder, ZIP archive or single audio file)
        is_file = os.path.isfile(input_dir)
        if not input_dir or not (is_file or os.path.isdir(input_dir)):
            yield sse(status='error', code='input_invalid', msg='Input must be an existing folder, ZIP file or audio file.')
            return

        # Normalize & Create Output Directory
        if create_folder:
            # "<input name>_mp3" or "_wav", inside the chosen output folder (default: next to the input)
            norm_input_dir = os.path.normpath(os.path.abspath(input_dir))
            parent_dir = output_dir or os.path.dirname(norm_input_dir)
            input_name = os.path.basename(norm_input_dir)
            if is_file:
                input_name = os.path.splitext(input_name)[0]
            norm_output_dir = os.path.normpath(os.path.join(parent_dir, input_name + '_' + fmt))
        elif not output_dir:
            yield sse(status='error', code='output_missing', msg='Please specify an output directory.')
            return
        else:
            norm_output_dir = os.path.normpath(output_dir)

        if not os.path.exists(norm_output_dir):
            if create_folder:
                try:
                    os.makedirs(norm_output_dir, exist_ok=True)
                except Exception as e:
                    yield sse(status='error', code='output_create_failed', detail=str(e), msg=f'Failed to create output folder: {str(e)}')
                    return
            else:
                yield sse(status='error', code='output_not_found', msg='Output directory does not exist.')
                return
        elif not os.path.isdir(norm_output_dir):
            yield sse(status='error', code='output_is_file', msg='Target output path is a file, not a directory.')
            return

        # Find the audio files
        if is_file and zipfile.is_zipfile(input_dir):
            yield sse(status='info', code='extracting', msg='Extracting ZIP archive...')
        try:
            sources = find_sources(input_dir, AUDIO_EXTENSIONS, temp_dirs)
        except Exception as e:
            yield sse(status='error', code='zip_failed', detail=str(e), msg=f'Failed to read the source: {str(e)}')
            return

        if sources is None:
            yield sse(status='error', code='input_invalid', msg='Input must be an existing folder, ZIP file or audio file.')
            return
        if not sources:
            yield sse(status='error', code='no_audio', msg='No audio files found in the input.')
            return

        if not FFMPEG:
            yield sse(status='error', code='ffmpeg_missing', msg='FFmpeg not found. Ensure it is installed and in your PATH.')
            return

        # Files that would produce the same output (same name in two folders, or song.flac + song.wav) get a number
        stems = [os.path.splitext(os.path.basename(source_id))[0] for _, source_id in sources]
        items = [(path, source_id, stem, out_stem) for (path, source_id), stem, out_stem in zip(sources, stems, unique_names(stems))]
        if only is not None:
            items = [item for item in items if item[1] in only]

        yield sse(status='start', total=len(items), output_dir=norm_output_dir)

        # SoundCloud lookups are network-bound; keep them few to avoid rate limiting
        tag_slots = threading.Semaphore(4)

        def convert_one(item):
            in_path, source_id, stem, out_stem = item
            filename = os.path.basename(in_path)
            out_name = out_stem + '.' + fmt
            out_path = os.path.join(norm_output_dir, out_name)

            # A WAV converted into its own folder would be overwritten while it is being read
            if os.path.normcase(os.path.abspath(out_path)) == os.path.normcase(os.path.abspath(in_path)):
                return {'status': 'warning', 'code': 'same_file', 'file': filename, 'id': source_id,
                        'msg': f'Skipped {filename}: the output would overwrite the source'}

            def command(with_art):
                # Tags live on the container for most formats and on the audio stream for Ogg/Opus: take both
                cmd = [FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-i', in_path, '-map', '0:a:0']
                if fmt == 'wav':
                    # Keep 24-bit sources in 24 bits; everything else (CD audio, lossy formats) fits in 16
                    pcm = 'pcm_s24le' if bits_per_sample(in_path) > 16 else 'pcm_s16le'
                    return cmd + ['-c:a', pcm, '-map_metadata', '0', '-map_metadata', '0:s:a:0', out_path]
                if with_art:
                    cmd.extend(['-map', '0:v?', '-c:v', 'copy'])
                return cmd + ['-ab', '320k', '-map_metadata', '0', '-map_metadata', '0:s:a:0', '-id3v2_version', '3', out_path]

            try:
                # Keep embedded cover art when the MP3 can hold it, otherwise convert the audio alone
                code, stderr = job.run(command(True))
                if code != 0 and fmt == 'mp3' and not job.cancelled.is_set():
                    code, stderr = job.run(command(False))
            except OSError as e:
                code, stderr = 1, str(e)

            if code == 0 and fmt == 'wav':
                # FFmpeg only writes basic tags into a WAV, and no cover art: add a full ID3 chunk
                try:
                    copy_tags_to_wav(in_path, out_path)
                except Exception:
                    pass

            if code != 0:
                # Don't leave a truncated file behind
                try:
                    os.remove(out_path)
                except OSError:
                    pass
                if job.cancelled.is_set():
                    return None
                detail = error_line(stderr)
                return {'status': 'warning', 'code': 'convert_failed', 'file': filename, 'id': source_id, 'detail': detail,
                        'msg': f'Failed to convert {filename}: {detail}'}

            event = {'status': 'success', 'file': filename, 'id': source_id}
            if out_stem != stem:
                event['renamed'] = out_name
            if tag and not job.cancelled.is_set():
                try:
                    with tag_slots:
                        event['tag'] = tag_file(out_path, stem)
                    event['tag_msg'] = describe_tag_result(event['tag'])
                except Exception as e:
                    event['tag_error'] = str(e)
            return event

        # One FFmpeg process per core; each worker converts a file, then tags it
        yield from work(job, items, convert_one, os.cpu_count() or 4)

        yield sse(status='cancelled' if job.cancelled.is_set() else 'done', output_dir=norm_output_dir)

    return stream_run(run)

@app.route('/tag', methods=['POST'])
def tag():
    """Tags existing MP3s and WAVs in place: a folder of them, or a single file."""
    data = request.json
    input_dir = data.get('input_dir', '').strip()
    only = data.get('only')

    def run(job, temp_dirs):
        sources = find_sources(input_dir, TAGGABLE_EXTENSIONS, temp_dirs, allow_zip=False) if input_dir else None
        if sources is None:
            yield sse(status='error', code='tag_input_invalid', msg='Input must be an existing folder, MP3 or WAV file.')
            return
        if only is not None:
            sources = [source for source in sources if source[1] in only]
        if not sources:
            yield sse(status='error', code='no_mp3', msg='No MP3 or WAV files found in the input.')
            return

        output_dir = input_dir if os.path.isdir(input_dir) else os.path.dirname(input_dir)
        yield sse(status='start', total=len(sources), output_dir=output_dir)

        def tag_one(source):
            path, source_id = source
            event = {'status': 'success', 'file': source_id, 'id': source_id}
            try:
                event['tag'] = tag_file(path)
                event['tag_msg'] = describe_tag_result(event['tag'])
            except Exception as e:
                event['tag_error'] = str(e)
            return event

        # SoundCloud lookups are network-bound; keep them few to avoid rate limiting
        yield from work(job, sources, tag_one, 4)

        yield sse(status='cancelled' if job.cancelled.is_set() else 'done', output_dir=output_dir)

    return stream_run(run)

def port_is_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # Without this, Windows lets two programs listen on the same port
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            return False
    return True

def start_server():
    """Serves the app on a background thread; returns its URL."""
    from werkzeug.serving import make_server
    # The usual port keeps the saved language and theme, which the browser stores per address
    port = int(os.environ.get('PORT', DEFAULT_PORT))
    server = make_server('127.0.0.1', port if port_is_free(port) else 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"

def on_drop(event):
    """Hands the path of a dropped file or folder to the page, which can't read it itself."""
    files = event.get('dataTransfer', {}).get('files', [])
    paths = [f['pywebviewFullPath'] for f in files if f.get('pywebviewFullPath')]
    if paths:
        window.evaluate_js(f"onDropPath({json.dumps(os.path.normpath(paths[0]))})")

def on_loaded():
    from webview.dom import DOMEventHandler
    window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)

def stop_jobs():
    """Stops running conversions and gives them a moment to remove their temp files."""
    for job in list(JOBS.values()):
        job.cancel()
    deadline = time.time() + 5
    while JOBS and time.time() < deadline:
        time.sleep(0.1)

def run_in_browser(url):
    """Fallback when no native window can be created: use the browser, and a small dialog to quit."""
    import tkinter as tk
    from tkinter import messagebox
    webbrowser.open(url)
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    messagebox.showinfo(APP_NAME, f"{APP_NAME} is open in your browser:\n{url}\n\nClick OK to quit.")
    root.destroy()

def main():
    global window

    if '--browser' in sys.argv:
        # Development: plain web server with the debug reloader, opened in any browser
        port = int(os.environ.get('PORT', DEFAULT_PORT))
        print(f"Starting Web Server. Open http://127.0.0.1:{port} in your browser.")
        app.run(debug=True, port=port)
        return

    url = start_server()
    try:
        import webview
        window = webview.create_window(APP_NAME, url, width=760, height=920, min_size=(420, 560), text_select=True)
        window.events.loaded += on_loaded
        # Not private, so the saved language and theme survive a restart
        if MAC:
            data_dir = os.path.expanduser('~/Library/Application Support')
        else:
            data_dir = os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()
        # Named after the app's former name, so settings saved before the rename are kept
        storage = os.path.join(data_dir, 'MP3Converter')
        webview.start(gui='edgechromium' if WINDOWS else None, private_mode=False, storage_path=storage, icon=ICON)
    except Exception:
        window = None
        run_in_browser(url)
    finally:
        stop_jobs()

if __name__ == '__main__':
    main()
