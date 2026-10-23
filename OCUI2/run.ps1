# Runs the window straight from the project with the engine in engine\ (no export): the quickest way to try a change.
# Usage:  powershell -ExecutionPolicy Bypass -File run.ps1 [<folder | pack folder | file.oczip>]
param([string]$Place = "")
$ErrorActionPreference = "Stop"
# The console in UTF-8: dotnet and Godot print UTF-8, and a GBK/Big5 code page would show it as garbage.
$OutputEncoding = [Console]::OutputEncoding = [Console]::InputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null
$env:PYTHONUTF8 = "1"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$godot = Join-Path $here "engine\godot\Godot_v4.7.2-stable_mono_win64\Godot_v4.7.2-stable_mono_win64_console.exe"
if (-not (Test-Path $godot)) { throw "No engine in engine\godot: run engine\get_engine.ps1 first." }
$project = Join-Path $here "OverCompressingUI"
Push-Location $project
try {
    & dotnet build -c Debug | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "dotnet build failed" }
    if ($Place) { & $godot --path . ++ $Place } else { & $godot --path . }
} finally {
    Pop-Location
}
