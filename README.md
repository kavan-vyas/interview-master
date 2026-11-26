# College Interview

A mock Cambridge maths interview that runs on your own computer. An AI professor asks you a real
interview-style problem out loud, listens to you think, gives hints when you are stuck, and pushes you
further when you are doing well. You work on a whiteboard (on an iPad, or in a browser window), and at the
end you get a written report.

Everything runs locally except the AI itself, which is Claude, used through **your own Claude account**
(Claude Code sign-in). No API keys are needed and nothing is stored in this repository.

- Voice: Kokoro (open source text-to-speech, runs on your computer)
- Listening: whisper.cpp (open source speech-to-text, runs on your computer)
- Whiteboard: tldraw, in the browser (iPad Safari works well)

## What you need

| | |
|---|---|
| Computer | A Mac (Apple Silicon M1 or newer, or Intel) on macOS 12+, or a Windows 10/11 64-bit PC |
| Disk space | About 6 GB free (Python packages + speech models) |
| Memory | 8 GB RAM minimum, 16 GB recommended |
| Microphone | Built-in is fine. **Headphones are strongly recommended**, so the microphone does not hear the professor |
| Claude | A Claude Pro or Max plan, and Claude Code installed (the setup tells you how) |
| Optional | An iPad on the same network, for writing on the whiteboard with a pencil |

Speed: Apple Silicon Macs are the fastest. Intel Macs and Windows PCs run the speech models on the
processor, so the professor takes a little longer to understand you.

## Install on a Mac

Open **Terminal** (press Cmd+Space, type Terminal, press Return) and paste:

```bash
git clone https://github.com/kavan-vyas/interview-master.git ~/college-interview && cd ~/college-interview && scripts/setup.sh
```

If your Mac asks to install "command line developer tools", click Install, wait for it to finish, then paste
the line again.

If you don't have [Homebrew](https://brew.sh) yet, setup stops and shows you the one line that installs it.
Paste that, close and reopen Terminal, then run `cd ~/college-interview && scripts/setup.sh` again.

What setup does (it is safe to run again at any time; it only fills in what is missing):

1. Installs whisper.cpp, ffmpeg and a small audio-output switcher with Homebrew
2. Finds or installs Python 3.11 and creates a private environment in `.venv` (nothing is installed system-wide)
3. Installs the Python packages from `requirements.txt`
4. Downloads the speech models into `models/` (about 2.3 GB; an interrupted download resumes)
5. Checks Claude Code and tells you if you need to install it or sign in
6. Builds a **College Interview** app in `~/Applications` that you can keep in the Dock

## Install on Windows

> The Windows path has been written carefully but **has not yet been tested on a real Windows PC by the author**.
> If something goes wrong, please open an issue with the message you see.

1. Install **Git for Windows** (also needed by Claude Code). Open **PowerShell** (Start menu, type PowerShell) and run:

   ```powershell
   winget install -e --id Git.Git
   ```

   Close PowerShell and open it again afterwards.

2. Paste this into PowerShell:

   ```powershell
   git clone https://github.com/kavan-vyas/interview-master.git $HOME\college-interview; cd $HOME\college-interview; powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
   ```

What setup does (safe to run again):

1. Finds Python 3.11, or installs it for your user with winget
2. Creates a private environment in `.venv` and installs the Python packages (the CPU version of PyTorch)
3. Downloads the official whisper.cpp Windows build into `tools\whisper`
4. Downloads the speech models into `models\`
5. Checks Claude Code and tells you if you need to install it or sign in
6. Adds **College Interview** shortcuts to your Desktop and Start Menu

## Sign in to Claude

The professor is Claude, running through Claude Code with your own Claude plan.

1. Install Claude Code if setup said it is missing:
   - Mac: `curl -fsSL https://claude.ai/install.sh | bash`
   - Windows (PowerShell): `irm https://claude.ai/install.ps1 | iex`
2. Close and reopen your terminal, then sign in:

   ```
   claude auth login
   ```

   A browser window opens; sign in with your Claude account. You only need to do this once.
3. Check it worked: `claude auth status` should show `"loggedIn": true`.

## First run

- **Mac:** open **College Interview** from `~/Applications` (or Launchpad). Drag it to the Dock to keep it handy.
- **Windows:** double-click the **College Interview** shortcut on the Desktop or in the Start Menu.
- Or from a terminal in the project folder: `.venv/bin/python launch.py open` (Mac) or
  `.venv\Scripts\python.exe launch.py open` (Windows).

The dashboard opens in your browser at http://localhost:8000. The first start takes a little longer while
the models load. Your computer will ask for **microphone access**: click Allow.

Put on headphones, pick your microphone, and start an interview. When you close the last dashboard tab the
app stops by itself. To stop it right away:

```
python launch.py stop       (use .venv/bin/python on Mac, .venv\Scripts\python.exe on Windows)
python launch.py status     is it running?
python launch.py doctor     check the whole install and say what is missing
```

## Connect an iPad for the whiteboard

1. Start an interview. The "Connect your iPad" screen shows QR codes.
2. **Same Wi-Fi (home):** scan the **Home Wi-Fi** code with the iPad camera. The board opens in Safari;
   tap the fullscreen button (or Share, Add to Home Screen).
3. **University Wi-Fi (eduroam and similar):** these networks stop devices from seeing each other, so the
   home code will not load. Use [Tailscale](https://tailscale.com) instead (free for personal use):
   install Tailscale on the computer and the iPad, sign in to the same account on both, then scan the
   **Tailscale** code.
4. No iPad? Press "Open board on this Mac" (the same button works on Windows) and use a mouse, trackpad or
   graphics tablet.

On Windows, the first time an iPad connects, Windows Firewall may ask about Python: allow it on
**Private** networks.

## Troubleshooting

Start with `python launch.py doctor` (see above). It checks Python, packages, whisper-server, the models and
your Claude sign-in, and prints the fix for anything missing.

| Problem | Fix |
|---|---|
| The professor can't hear me / no microphone in the list | **Mac:** System Settings, Privacy & Security, Microphone: turn on College Interview (and Terminal, if you started it from Terminal). Then stop and start the app. **Windows:** Settings, Privacy & security, Microphone: turn on "Microphone access" and "Let desktop apps access your microphone". |
| The professor hears himself and interrupts | Use headphones. Laptop speakers leak into the microphone. |
| "Port 8000 is already used by another program" | Another program (or an old copy) is using port 8000. Run `python launch.py stop`, or restart the computer. To use another port: set `IM_PORT=8010` before starting (Mac: `IM_PORT=8010 .venv/bin/python launch.py open`; Windows PowerShell: `$env:IM_PORT=8010; .venv\Scripts\python.exe launch.py open`). |
| "whisper-server or model missing" | **Mac:** `brew install whisper-cpp`, then run `scripts/setup.sh` again. **Windows:** run `scripts\setup.ps1` again; it puts `whisper-server.exe` in `tools\whisper`. If whisper-server will not start on Windows, install the [Microsoft Visual C++ runtime](https://aka.ms/vs/17/release/vc_redist.x64.exe). |
| The professor doesn't answer, or "not logged in" / "authentication" errors | Your Claude sign-in expired. Run `claude auth login` and sign in again. `claude auth status` should show `"loggedIn": true`. |
| `claude: command not found` | Install Claude Code (see Sign in to Claude), then close and reopen the terminal. |
| The app did not start | Read the log. **Mac:** `~/Library/Logs/CollegeInterview.log`. **Windows:** `%LOCALAPPDATA%\CollegeInterview\Logs\CollegeInterview.log`. |
| iPad page won't load | On uni Wi-Fi use the Tailscale code. At home, check both devices are on the same Wi-Fi network (not a guest network). |
| A model download keeps failing | Run setup again; it resumes where it stopped. |
| No professor voice, log says "Error processing file ... espeak-ng-data/phontab" | The project folder path is too long for the voice engine. Move the folder somewhere short (like `~/college-interview` or `C:\Users\you\college-interview`), delete `.venv`, and run setup again. |

## Updating

```
cd ~/college-interview
git pull
scripts/setup.sh                                                  (Mac)
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1        (Windows)
```

## Uninstall

1. Stop the app: `python launch.py stop` (with the `.venv` Python, as above).
2. Delete the project folder (for example `~/college-interview`). This removes the Python environment,
   the models and **your saved interviews and reports** (in `sessions/`), so copy that folder first if you
   want to keep them.
3. **Mac:** delete `~/Applications/College Interview.app`, `~/Library/Application Support/CollegeInterview`
   and `~/Library/Logs/CollegeInterview.log`. Optional: `brew uninstall whisper-cpp switchaudio-osx`.
4. **Windows:** delete the College Interview shortcuts on the Desktop and in the Start Menu, and the folder
   `%LOCALAPPDATA%\CollegeInterview`.
5. Claude Code is a separate program; keep it or uninstall it as you like.

## For developers

| File | Job |
|---|---|
| `server.py` | FastAPI + WebSocket server, dashboard and board |
| `orchestrator.py`, `professor.py` | interview state, rules, timers; the AI professor's turns |
| `audio.py` | microphone, Silero voice activity, whisper-server, ECAPA speaker match |
| `tts/voice.py` | Kokoro voice with maths-aware speech |
| `ai.py` | Claude calls through the Claude Agent SDK (your Claude Code login) |
| `report.py` | `sessions/<date-time>/` transcript and report |
| `static/` | dashboard; `static/tl` is the prebuilt tldraw board from `board-src/` |
| `launch.py` | cross-platform start / stop / status / doctor |
| `scripts/setup.sh`, `scripts/setup.ps1` | installers; `scripts/fetch_models.py` downloads the models |
| `scripts/ci.sh`, `scripts/make_app.sh` | Mac Dock app launcher and builder |

- Dry run without a microphone: `.venv/bin/python server.py --no-audio --sim sim/demo.txt`
- Tunables live in `config.py`; override with environment variables `IM_<NAME>` (e.g. `IM_HOLD_S=30`).
- On Apple Silicon, `tts/stt_worker.py` (MLX Whisper) is an optional faster speech-to-text worker: it is used
  when a Python with `mlx-whisper` exists at `tts/.venv/bin/python`; otherwise whisper.cpp is used.
