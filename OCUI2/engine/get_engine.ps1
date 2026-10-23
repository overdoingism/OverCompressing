# Fetches the engine this project builds with: Godot 4.7.2 (.NET edition) and the Windows export templates, from the
# official GitHub release, checked against the release's SHA-512 sums. Everything lands in this folder (gitignored):
#   engine\godot\Godot_v4.7.2-stable_mono_win64\   the editor (also runs the project: run.ps1)
#   engine\templates\windows_*_x86_64.exe          the export templates build.ps1 exports with
# The templates archive is 1.1 GB, so only its four Windows members are fetched, by HTTP range requests
# (fetch_template.py). Needs: PowerShell 5+, and Python 3 on PATH (any version) for the partial fetch.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = "4.7.2-stable"
$base = "https://github.com/godotengine/godot/releases/download/$version"
$download = Join-Path $here "download"
New-Item -ItemType Directory -Force $download | Out-Null

$sums = Join-Path $download "SHA512-SUMS.txt"
Invoke-WebRequest "$base/SHA512-SUMS.txt" -OutFile $sums
$zipName = "Godot_v${version}_mono_win64.zip"
$zip = Join-Path $download $zipName
if (-not (Test-Path $zip)) {
    Write-Host "Downloading $zipName (about 110 MB)..."
    Invoke-WebRequest "$base/$zipName" -OutFile $zip
}
$expected = (Get-Content $sums | Where-Object { $_ -like "*$zipName" }).Split(" ")[0]
$actual = (Get-FileHash $zip -Algorithm SHA512).Hash.ToLower()
if ($actual -ne $expected) { throw "SHA-512 mismatch for $zipName" }
Write-Host "Checksum OK"
Expand-Archive $zip -DestinationPath (Join-Path $here "godot") -Force

$templates = Join-Path $here "templates"
if (-not (Test-Path (Join-Path $templates "windows_release_x86_64.exe"))) {
    Write-Host "Fetching the Windows export templates..."
    & python -I (Join-Path $here "fetch_template.py") "$base/Godot_v${version}_mono_export_templates.tpz" $templates `
        templates/windows_release_x86_64.exe templates/windows_debug_x86_64.exe `
        templates/windows_release_x86_64_console.exe templates/windows_debug_x86_64_console.exe
    if ($LASTEXITCODE -ne 0) { throw "template fetch failed" }
}
Write-Host "Engine ready in $here"
