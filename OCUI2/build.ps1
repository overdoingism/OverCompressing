# Builds dist\OverCompressingUI.exe (Windows x64, release) with the Godot editor in engine\ (see engine\get_engine.ps1),
# then zips it with its data_* folder into dist\OverCompressingUI-<version>-win64.zip: the file that goes up as a
# GitHub release asset. The exe is not committed: it is over GitHub's 100 MB limit for a file in a repository, and it
# only runs with the 200-odd files of the data_* folder beside it (the .NET runtime and the assemblies).
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1 [-Debug] [-NoZip]
param([switch]$Debug, [switch]$NoZip)
$ErrorActionPreference = "Stop"
# The console in UTF-8: dotnet and Godot print UTF-8, and a GBK/Big5 code page would show it as garbage.
$OutputEncoding = [Console]::OutputEncoding = [Console]::InputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null
$env:PYTHONUTF8 = "1"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$godot = Join-Path $here "engine\godot\Godot_v4.7.2-stable_mono_win64\Godot_v4.7.2-stable_mono_win64_console.exe"
if (-not (Test-Path $godot)) { throw "No engine in engine\godot: run engine\get_engine.ps1 first." }
$project = Join-Path $here "OverCompressingUI"
$dist = Join-Path $here "dist"
# A clean dist: the export leaves ~RF*.TMP files behind where it replaced assemblies, and nothing old may go into the zip.
if (Test-Path $dist) {
    try { Get-ChildItem $dist -Force | Remove-Item -Recurse -Force }
    catch { throw "dist\ cannot be emptied (is OverCompressingUI.exe still running?): $_" }
}
New-Item -ItemType Directory -Force $dist | Out-Null
$out = Join-Path $dist "OverCompressingUI.exe"
Push-Location $project
try {
    & dotnet build -c $(if ($Debug) { "Debug" } else { "ExportRelease" }) | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "dotnet build failed" }
    $mode = if ($Debug) { "--export-debug" } else { "--export-release" }
    & $godot --headless --path . $mode "Windows Desktop" $out | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "godot export failed" }
} finally {
    Pop-Location
}
if (-not (Test-Path $out)) { throw "godot export left no $out" }
Write-Host "Built $out"
Get-ChildItem $dist | Format-Table Name, Length -AutoSize | Out-Host

if (-not $NoZip) {
    # The release asset: the exe and its data_* folder, named by the window's version (T.GuiVersion in Text.cs).
    $text = Get-Content (Join-Path $project "src\Core\Text.cs") -Raw
    if ($text -notmatch 'GuiVersion = "([^"]+)"') { throw "No GuiVersion in src\Core\Text.cs" }
    $version = $Matches[1]
    $data = Get-ChildItem $dist -Directory -Filter "data_*"
    if (-not $data) { throw "No data_* folder beside the exe" }
    $zip = Join-Path $dist ("OverCompressingUI-$version-win64" + $(if ($Debug) { "-debug" } else { "" }) + ".zip")
    Compress-Archive -Path $out, $data.FullName -DestinationPath $zip -CompressionLevel Optimal -Force
    $mb = [math]::Round((Get-Item $zip).Length / 1MB)
    Write-Host "Zipped $zip ($mb MB): the release asset. Upload it with the release; it is not committed."
}
