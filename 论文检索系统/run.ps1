param([int]$Port = 8501, [switch]$Headless)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$projectPython = if (Test-Path -LiteralPath $localPython) { $localPython } else { (Get-Command python -ErrorAction Stop).Source }
& $projectPython -c 'import streamlit'
if ($LASTEXITCODE -ne 0) {
    Write-Host ('Install dependencies first: "' + $projectPython + '" -m pip install -r requirements.txt')
    exit 1
}
if (-not (Test-Path -LiteralPath 'paper-data\data\papers_final.jsonl') -and -not (Test-Path -LiteralPath 'paper-data\data\papers.jsonl')) {
    Write-Host 'Missing formal dataset in paper-data/data. Restore it before starting.'
    exit 1
}
$headlessValue = if ($Headless) { 'true' } else { 'false' }
& $projectPython -m streamlit run app.py --server.address 127.0.0.1 --server.port $Port --server.headless $headlessValue
exit $LASTEXITCODE
