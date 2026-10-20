# Offline install of the OverCompressing restore environment on Windows x64. No network is used.
# Usage:  powershell -ExecutionPolicy Bypass -File install_windows.ps1 [-Venv <dir>] [-Python <python.exe>]
#                                                                       [-InstallPython] [-PythonDir <dir>]
# Python 3.13 is taken from -Python, else `py -3.13`, else `python` on PATH. If none is 3.13 (or -InstallPython
# is given), the bundled python.org installer is checked (Authenticode + SHA-256) and installed for the current
# user only into -PythonDir, without touching PATH, file associations or the py launcher.
param(
    [string]$Venv = (Join-Path $HOME 'overcompressing-venv'),
    [string]$Python = '',
    [switch]$InstallPython,
    [string]$PythonDir = (Join-Path $HOME 'overcompressing-python313')
)
$ErrorActionPreference = 'Stop'
$kit = $PSScriptRoot
$installer = Join-Path $kit 'python\windows\python-3.13.16-amd64.exe'
$installerSha256 = 'FB4F9F5D438B2396DA0086DC70B935C530CB578E37ADC6D354F7AD2037FEE83B'

# No double quotes inside -c code: Windows PowerShell 5.1 strips them when calling native programs.
function Get-PyVersion([string]$exe) {
    try { return (& $exe -c 'import sys, platform; print(*sys.version_info[:2], platform.machine())' 2>$null) } catch { return '' }
}

if (-not $Python -and -not $InstallPython) {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += (& py -3.13 -c 'import sys; print(sys.executable)' 2>$null)
    }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    $candidates += (Join-Path $PythonDir 'python.exe')  # a previous run of this script
    $Python = $candidates | Where-Object { $_ -and (Test-Path $_) -and ((Get-PyVersion $_) -eq '3 13 AMD64') } | Select-Object -First 1
}

if (-not $Python) {
    Write-Host "No Python 3.13 found; installing the bundled python.org installer into $PythonDir"
    $sig = Get-AuthenticodeSignature $installer
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notlike 'CN=Python Software Foundation,*') {
        throw "Installer signature check failed: $($sig.Status) $($sig.SignerCertificate.Subject)"
    }
    if ((Get-FileHash $installer -Algorithm SHA256).Hash -ne $installerSha256) { throw 'Installer SHA-256 mismatch' }
    $installArgs = @('/quiet', 'InstallAllUsers=0', "TargetDir=`"$PythonDir`"", 'Include_launcher=0', 'InstallLauncherAllUsers=0',
                     'PrependPath=0', 'Shortcuts=0', 'AssociateFiles=0', 'Include_test=0', 'Include_doc=0')
    $p = Start-Process -FilePath $installer -ArgumentList $installArgs -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Python installer failed with exit code $($p.ExitCode)" }
    $Python = Join-Path $PythonDir 'python.exe'
}

$ver = Get-PyVersion $Python
if ($ver -ne '3 13 AMD64') { throw "Need CPython 3.13 x64, found '$ver' at $Python" }
Write-Host "Using $Python"

& $Python -m venv $Venv
$py = Join-Path $Venv 'Scripts\python.exe'
& $py -m pip install --no-index --find-links (Join-Path $kit 'wheels\win_amd64') -r (Join-Path $kit 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
& $py -I -c 'import numpy, zstandard'
if ($LASTEXITCODE -ne 0) { throw 'installed packages failed to import' }
Write-Host 'restore environment OK'

Write-Host ""
Write-Host "Restore a model:   & '$py' -I '$kit\tools\llm_archive.py' restore '$(Split-Path $kit -Parent)' <name> <dest_dir>"
Write-Host "Check the archive: & '$py' -I '$kit\tools\llm_archive.py' verify '$(Split-Path $kit -Parent)'"
Write-Host "Or use a window:  & '$(Join-Path $Venv 'Scripts\pythonw.exe')' -I '$kit\tools\llm_archive_gui.py'"
