<#
    Arrowhead Paesano — generate the Chiefs Narrative locally (Windows / PowerShell).

    Usage:
        ./tools/run_local.ps1                 # auto-detect provider, regenerate, rebuild site
        ./tools/run_local.ps1 -Provider offline
        ./tools/run_local.ps1 -NoBuild        # regenerate data only, skip Hugo build
        ./tools/run_local.ps1 -Serve          # regenerate, then run the Hugo dev server

    Reads tools/.env if present. Requires Python 3.10+ and (for build/serve) Node + hugo-bin.
#>
param(
    [string]$Provider = "",
    [switch]$NoBuild,
    [switch]$Serve
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# Load tools/.env into the process environment (KEY=VALUE lines).
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Write-Host "Loading environment from tools/.env"
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $idx = $line.IndexOf("=")
            $key = $line.Substring(0, $idx).Trim()
            $val = $line.Substring($idx + 1).Trim().Trim('"')
            [Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
}

# Ensure dependencies.
python -m pip install -q -r (Join-Path $PSScriptRoot "requirements.txt")

# Generate.
$genArgs = @("-m", "tools.chiefs_narrative.generate")
if ($Provider) { $genArgs += @("--provider", $Provider) }
Write-Host "Generating the Chiefs Narrative..."
python @genArgs

if ($NoBuild -and -not $Serve) { exit 0 }

# Build or serve the Hugo site.
$hugo = Join-Path $repo "node_modules/.bin/hugo.cmd"
if (-not (Test-Path $hugo)) { $hugo = "hugo" }

if ($Serve) {
    & $hugo server --bind 127.0.0.1 --port 1515 --baseURL http://localhost:1515/ --disableFastRender --renderToMemory
} else {
    & $hugo --gc --minify
    Write-Host "Site built to dist/."
}
