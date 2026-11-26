# College Interview: one-command install for Windows 10/11 (64-bit). Safe to run again:
# anything already installed or downloaded is kept, so a re-run only fills in what is missing.
#
# Run from the project folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# Installs into this folder only: .venv (Python packages), tools\whisper (speech-to-text engine), models\.
# Adds "College Interview" shortcuts to the Desktop and Start Menu.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"          # Invoke-WebRequest is far faster without the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

# Pinned whisper.cpp build (official ggml-org release). If it ever disappears, the newest release is used.
$WhisperTag = "b5907"
$WhisperAsset = "whisper-bin-x64.zip"

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host ""; Write-Host "Setup stopped: $msg" -ForegroundColor Red; exit 1 }
function Check($what) { if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit code $LASTEXITCODE). Scroll up for the error." } }

Write-Host "College Interview setup in $Root"
if (-not [Environment]::Is64BitOperatingSystem) { Fail "a 64-bit version of Windows is needed." }
if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") {
    Write-Host "Note: Windows on ARM is not tested. Setup will try the x64 versions (they run under emulation)." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- Python 3.11
Step "Python 3.11"
# Run a program and return its first line of output ("" if it fails). Never stops the script.
function Get-Line($exe, [string[]]$argv) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $out = & $exe @argv 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return ([string]($out | Select-Object -First 1)).Trim() }
    } catch {} finally { $ErrorActionPreference = $old }
    return ""
}
function Get-PyVersion($exe) { return (Get-Line $exe @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")) }
function Find-Python311 {
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $p = Get-Line "py" @("-3.11", "-c", "import sys; print(sys.executable)")
        if ($p) { $cands += $p }
    }
    $cands += @(
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    foreach ($c in $cands) {
        if ($c -and (Test-Path $c) -and ((Get-PyVersion $c) -eq "3.11")) { return $c }
    }
    return $null
}
$Py = Find-Python311
if (-not $Py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Python 3.11 not found: installing it with winget (just for your user account)..."
        winget install -e --id Python.Python.3.11 --scope user
        $Py = Find-Python311
    }
    if (-not $Py) {
        Fail "Python 3.11 is needed. Install it from https://www.python.org/downloads/release/python-3119/ (Windows installer 64-bit), then run this script again."
    }
}
Write-Host "using $Py"

# ---------------------------------------------------------------- .venv + packages
Step "Python packages (into .venv; first time takes several minutes, about 1.5 GB)"
$VPy = Join-Path $Root ".venv\Scripts\python.exe"
$ok = $false
if (Test-Path $VPy) { $ok = ((Get-PyVersion $VPy) -eq "3.11") }
if (-not $ok) {
    if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" }
    & $Py -m venv .venv; Check "Creating .venv"
}
& $VPy -m pip install --upgrade --quiet pip; Check "Updating pip"
& $VPy -m pip install -r requirements.txt; Check "Installing Python packages"

# ---------------------------------------------------------------- whisper.cpp (whisper-server.exe)
Step "Speech-to-text engine (whisper.cpp)"
$WDir = Join-Path $Root "tools\whisper"
$WExe = Join-Path $WDir "whisper-server.exe"
if (Test-Path $WExe) {
    Write-Host "ok      tools\whisper\whisper-server.exe"
} else {
    $tmp = Join-Path $env:TEMP ("ci-whisper-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp $WhisperAsset
    $url = "https://github.com/ggml-org/whisper.cpp/releases/download/$WhisperTag/$WhisperAsset"
    try {
        Write-Host "downloading $url"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
    } catch {
        Write-Host "pinned build not available, looking up the newest whisper.cpp release..."
        $rels = Invoke-RestMethod -UseBasicParsing -Uri "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=20" -Headers @{ "User-Agent" = "college-interview-setup" }
        $asset = $null
        foreach ($r in $rels) { foreach ($a in $r.assets) { if (-not $asset -and $a.name -eq $WhisperAsset) { $asset = $a } } }
        if (-not $asset) { Fail "could not find $WhisperAsset on https://github.com/ggml-org/whisper.cpp/releases" }
        Write-Host "downloading $($asset.browser_download_url)"
        Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zip
    }
    $ex = Join-Path $tmp "x"
    Expand-Archive -Path $zip -DestinationPath $ex -Force
    $found = Get-ChildItem -Path $ex -Recurse -Filter "whisper-server.exe" | Select-Object -First 1
    if (-not $found) { Fail "whisper-server.exe was not in the downloaded zip." }
    New-Item -ItemType Directory -Force -Path $WDir | Out-Null
    Copy-Item -Path (Join-Path $found.DirectoryName "*") -Destination $WDir -Recurse -Force   # exe + its DLLs
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Write-Host "installed tools\whisper\whisper-server.exe"
}
# A missing Visual C++ runtime shows up as exit code -1073741515 (0xC0000135).
$p = Start-Process -FilePath $WExe -ArgumentList "--help" -NoNewWindow -Wait -PassThru -RedirectStandardOutput (Join-Path $env:TEMP "ci-wh-out.txt") -RedirectStandardError (Join-Path $env:TEMP "ci-wh-err.txt")
if ($p.ExitCode -eq -1073741515 -or $p.ExitCode -eq -1073741701) {
    Write-Host "whisper-server needs the Microsoft Visual C++ runtime." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Microsoft.VCRedist.2015+.x64
    } else {
        Write-Host "Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe and run this script again."
    }
}

# ---------------------------------------------------------------- models + folders
Step "Models (about 2.3 GB the first time; an interrupted download resumes)"
foreach ($d in @("models", "sessions", ".claude_sandbox")) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
$env:PYTHONUTF8 = "1"
& $VPy scripts\fetch_models.py; Check "Downloading models"

# ---------------------------------------------------------------- Claude Code
Step "Claude Code (the AI professor uses your own Claude sign-in)"
$env:Path = "$env:USERPROFILE\.local\bin;$env:APPDATA\npm;$env:Path"
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    Write-Host "found $($claude.Source)"
    $signed = $false
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $st = (& $claude.Source auth status 2>$null | Out-String)
        $signed = (($st | ConvertFrom-Json).loggedIn -eq $true)
    } catch {} finally { $ErrorActionPreference = $old }
    if ($signed) { Write-Host "signed in" }
    else {
        Write-Host "Not signed in yet. Run this, and sign in with your Claude account in the browser that opens:"
        Write-Host "  claude auth login"
    }
} else {
    Write-Host "Claude Code is not installed. Claude Code on Windows needs Git for Windows first:"
    Write-Host "  winget install -e --id Git.Git"
    Write-Host "Then install Claude Code (in PowerShell):"
    Write-Host "  irm https://claude.ai/install.ps1 | iex"
    Write-Host "then close PowerShell, open it again, and sign in (needs a Claude Pro or Max plan):"
    Write-Host "  claude auth login"
}

# ---------------------------------------------------------------- shortcuts
Step "Desktop and Start Menu shortcuts"
$PyW = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Icon = Join-Path $Root "static\favicon.ico"
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "College Interview.lnk"))
    $lnk.TargetPath = $PyW
    $lnk.Arguments = "`"$Root\launch.py`" open"
    $lnk.WorkingDirectory = $Root
    $lnk.Description = "College Interview: mock Cambridge maths interview"
    if (Test-Path $Icon) { $lnk.IconLocation = $Icon }
    $lnk.Save()
    Write-Host "created $(Join-Path $dir 'College Interview.lnk')"
}

Step "Checking the install"
& $VPy launch.py doctor

Write-Host ""
Write-Host "Setup finished." -ForegroundColor Green
Write-Host "Start College Interview from the Desktop or Start Menu shortcut, or run:"
Write-Host "  .venv\Scripts\python.exe launch.py open"
Write-Host "If Windows Firewall asks about Python, allow it on Private networks so the iPad board can connect."
