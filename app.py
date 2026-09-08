from flask import Flask, request, render_template, Response, jsonify
import os
import subprocess
import json
import tkinter as tk
from tkinter import filedialog

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/browse')
def browse():
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    
    folder_path = filedialog.askdirectory()
    root.destroy()
    
    if folder_path:
        folder_path = os.path.normpath(folder_path)
        
    return jsonify({'path': folder_path})

@app.route('/convert', methods=['POST'])
def convert():
    data = request.json
    input_dir = data.get('input_dir', '').strip()
    output_dir = data.get('output_dir', '').strip()
    create_folder = data.get('create_folder', False)

    def generate():
        # Validate Input Directory
        if not input_dir or not os.path.isdir(input_dir):
            yield f"data: {json.dumps({'status': 'error', 'msg': 'Input directory not found or invalid.'})}\n\n"
            return

        # Normalize & Create Output Directory
        if not output_dir:
            yield f"data: {json.dumps({'status': 'error', 'msg': 'Please specify an output directory.'})}\n\n"
            return

        norm_output_dir = os.path.normpath(output_dir)

        if not os.path.exists(norm_output_dir):
            if create_folder:
                try:
                    os.makedirs(norm_output_dir, exist_ok=True)
                except Exception as e:
                    yield f"data: {json.dumps({'status': 'error', 'msg': f'Failed to create output folder: {str(e)}'})}\n\n"
                    return
            else:
                yield f"data: {json.dumps({'status': 'error', 'msg': 'Output directory does not exist. Check \"Create output folder\" to auto-create it.'})}\n\n"
                return
        elif not os.path.isdir(norm_output_dir):
            yield f"data: {json.dumps({'status': 'error', 'msg': 'Target output path is a file, not a directory.'})}\n\n"
            return

        # Find both FLAC and WAV files
        valid_extensions = ('.flac', '.wav')
        audio_files = [f for f in os.listdir(input_dir) if f.lower().endswith(valid_extensions)]
        total_files = len(audio_files)

        if total_files == 0:
            yield f"data: {json.dumps({'status': 'error', 'msg': 'No FLAC or WAV files found in the input directory.'})}\n\n"
            return

        yield f"data: {json.dumps({'status': 'start', 'total': total_files})}\n\n"

        for i, filename in enumerate(audio_files, 1):
            in_path = os.path.join(input_dir, filename)
            out_name = os.path.splitext(filename)[0] + ".mp3"
            out_path = os.path.join(norm_output_dir, out_name)

            yield f"data: {json.dumps({'status': 'progress', 'current': i, 'total': total_files, 'file': filename})}\n\n"

            # Base FFmpeg command
            cmd = [
                'ffmpeg', '-y', '-i', in_path,
                '-ab', '320k', '-map_metadata', '0',
                '-id3v2_version', '3'
            ]

            # Preserve cover art stream if converting a FLAC file
            if filename.lower().endswith('.flac'):
                cmd.extend(['-map', '0:a', '-map', '0:v?', '-c:v', 'copy'])

            cmd.append(out_path)

            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                yield f"data: {json.dumps({'status': 'success', 'file': filename})}\n\n"
            except FileNotFoundError:
                yield f"data: {json.dumps({'status': 'error', 'msg': 'FFmpeg not found. Ensure it is installed and in your Windows PATH.'})}\n\n"
                return
            except subprocess.CalledProcessError:
                yield f"data: {json.dumps({'status': 'warning', 'msg': f'Failed to convert {filename}'})}\n\n"

        yield f"data: {json.dumps({'status': 'done'})}\n\n"

    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    print("Starting Web Server. Open http://127.0.0.1:5000 in your browser.")
    app.run(debug=True, port=5000)