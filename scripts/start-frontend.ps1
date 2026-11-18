$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot 'frontend'
if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'package.json'))) {
    throw 'React frontend package.json was not found.'
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules'))) {
    throw 'Frontend dependencies are missing. Run: npm --prefix frontend ci'
}
$npmCommand = Get-Command 'npm.cmd' -ErrorAction Stop
Push-Location -LiteralPath $projectRoot
try {
    & $npmCommand.Source --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort
    if ($LASTEXITCODE -ne 0) { throw "Frontend exited with code $LASTEXITCODE." }
} finally { Pop-Location }
