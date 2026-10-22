from flask import Flask, render_template, request, jsonify
import os, subprocess, threading, re, requests
from datetime import datetime

app = Flask(__name__, static_folder='static')
BASE_DIR = '/videos'
current_process = None
status_msg = "💤 Klaar om te starten..."
stop_flag = False
logs = []

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

def add_log(message):
    timestamp = datetime.now().strftime('%d-%m-%Y %H:%M:%S')
    logs.append(f"[{timestamp}] {message}")
    if len(logs) > 300: logs.pop(0)

def send_discord(message):
    webhook_url = os.environ.get('DISCORD_WEBHOOK', '').strip()
    if not webhook_url or not webhook_url.startswith('http'): return
    try: requests.post(webhook_url, json={"content": message}, timeout=5)
    except Exception as e: add_log(f"⚠️ Discord webhook fout: {str(e)}")

def get_duration(file_path):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    try: return float(subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode().strip())
    except Exception: return 0.0

def convert_videos(subfolder, target_ext):
    global current_process, status_msg, stop_flag
    stop_flag = False
    target_ext = target_ext.lower() if target_ext.startswith('.') else f".{target_ext.lower()}"
    search_path = os.path.join(BASE_DIR, subfolder)
    
    add_log(f"🚀 Nieuwe taak gestart: converteren naar {target_ext.upper()}")
    
    if not os.path.exists(search_path):
        msg = f"🤷 Map niet gevonden: {search_path}"
        status_msg = msg; add_log(msg); return

    status_msg = f"🔍 Scannen van mappen..."
    video_exts = ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.ts', '.m4v', '.mpg', '.mpeg', '.vob', '.3gp')
    files_to_convert = []

    for root, dirs, files in os.walk(search_path):
        dirs[:] = [d for d in dirs if d.lower() != 'featurettes']
        for file in files:
            if file.lower().endswith(video_exts) and not file.lower().endswith(target_ext):
                in_file = os.path.join(root, file)
                out_file = os.path.splitext(in_file)[0] + target_ext
                if not os.path.exists(out_file): files_to_convert.append((root, file))

    if not files_to_convert:
        add_log("🤷 Geen (nieuwe) video's gevonden.")
        status_msg = "🤷 Geen bestanden gevonden."
        current_process = None; return

    files_to_convert.sort(key=lambda x: (natural_sort_key(x[0]), natural_sort_key(x[1])))
    add_log(f"📋 {len(files_to_convert)} videobestand(en) gevonden. Starten...")
    
    # --- AUTO-DETECTIE HARDWARE ACCELERATIE ---
    has_gpu = os.path.exists('/dev/dri')
    if has_gpu:
        add_log("⚡ Hardware Acceleratie (Intel GPU) gevonden! Overschakelen naar QuickSync...")
    else:
        add_log("🐢 Let op: Geen GPU gevonden. Software encoding (CPU) is actief (maar ingesteld op Extra Snel).")

    current_dir = None

    for root, file in files_to_convert:
        if stop_flag:
            add_log("🛑 Proces afgebroken."); current_process = None; return
            
        current_dir = root
        in_file = os.path.join(root, file)
        out_file = os.path.splitext(in_file)[0] + target_ext
        duration = get_duration(in_file)
        status_msg = f"🎬 Starten met {file} (0%)"
        
        display_root = root.replace(BASE_DIR, '')
        if display_root == '': display_root = '/'
        add_log(f"📂 Map: {display_root} | 🎬 Start: {file}")
        
        last_log_percent = 0
        try:
            # --- HIER BEPAALT HIJ DE SNELHEID ---
            if target_ext in ['.mp4', '.mkv', '.mov', '.ts', '.m4v']:
                if has_gpu:
                    # H264_QSV (Intel QuickSync) is razendsnel en gebruikt de grafische chip
                    cmd = ['ffmpeg', '-y', '-hwaccel', 'qsv', '-i', in_file, '-c:v', 'h264_qsv', '-preset', 'faster', '-c:a', 'aac', out_file]
                else:
                    # Libx264 op "veryfast" is véél sneller dan de standaardinstelling
                    cmd = ['ffmpeg', '-y', '-i', in_file, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-c:a', 'aac', out_file]
            elif target_ext == '.mp3':
                cmd = ['ffmpeg', '-y', '-i', in_file, '-vn', '-c:a', 'libmp3lame', '-q:a', '2', out_file]
            else:
                cmd = ['ffmpeg', '-y', '-i', in_file, out_file]

            current_process = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True)
            for line in current_process.stderr:
                if stop_flag: break
                match = re.search(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})", line)
                if match and duration > 0:
                    h, m, s = match.groups()
                    current_time = int(h) * 3600 + int(m) * 60 + float(s)
                    percent = min(100, int((current_time / duration) * 100))
                    status_msg = f"🔄 Bezig met {file} ({percent}%) ⏳"
                    if percent >= last_log_percent + 25:
                        add_log(f"⏳ Voortgang {file}: {percent}% voltooid...")
                        last_log_percent = percent
            
            current_process.wait()
            if stop_flag:
                if os.path.exists(out_file): os.remove(out_file)
                status_msg = "🛑 Gestopt. Halffabrikaat verwijderd."; current_process = None; return
            add_log(f"✅ Succes! '{file}' is geconverteerd.")
        except Exception as e:
            err_msg = f"⚠️ Fout bij {file}: {str(e)}"; status_msg = err_msg; add_log(err_msg)

    if not stop_flag:
        add_log("🎉 Alle bestanden in de wachtrij zijn succesvol omgezet!")
        status_msg = "✅ Klaar! ✨"
        send_discord("🎉 **Wachtrij Voltooid!**\nAlle geselecteerde bestanden zijn succesvol omgezet! ✨")
        
    current_process = None

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/folders')
def get_folders():
    subpath = request.args.get('path', '').strip('/')
    if '..' in subpath: subpath = ''
    full_path = os.path.join(BASE_DIR, subpath)
    folders = []
    if os.path.exists(full_path):
        try:
            for entry in os.scandir(full_path):
                if entry.is_dir() and entry.name.lower() != 'featurettes': folders.append(entry.name)
        except: pass
    folders.sort(key=natural_sort_key)
    return jsonify({"current_path": subpath, "folders": folders})

@app.route('/api/logs')
def get_logs(): return jsonify(logs)

@app.route('/start', methods=['POST'])
def start():
    global current_process, stop_flag
    if current_process is not None and not stop_flag: return "Er is al een conversie bezig."
    folder = request.form.get('folder', '').strip('/')
    target = request.form.get('target', 'mp4')
    threading.Thread(target=convert_videos, args=(folder, target)).start()
    return "Gestart"

@app.route('/stop', methods=['POST'])
def stop():
    global current_process, status_msg, stop_flag
    stop_flag = True; add_log("🛑 Annuleringsverzoek verstuurd...")
    if current_process and hasattr(current_process, 'terminate'):
        try: current_process.terminate()
        except: pass
    status_msg = "⚠️ Conversie afbreken..."
    return "Gestopt"

@app.route('/status')
def status(): return status_msg

if __name__ == '__main__':
    add_log("🟢 Container opgestart.")
    app.run(host='0.0.0.0', port=5000)
