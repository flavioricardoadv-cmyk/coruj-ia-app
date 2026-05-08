param(
  [string]$Source = "",
  [string]$Categoria = "AUTO",
  [int]$Limit = 0,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Report = Join-Path $Root "modelo_db_export\motor_import_banco_manifestacoes.jsonl"

if (-not $Source) {
  $Desktop = [Environment]::GetFolderPath("Desktop")
  $Banco = Get-ChildItem -Path $Desktop -Directory |
    Where-Object {
      $_.Name -like "banco de manifesta*" -and
      (Test-Path (Join-Path $_.FullName "matheusx\Matheus\Matheus\Trabalhos antigos"))
    } |
    Select-Object -First 1
  if (-not $Banco) {
    throw "Pasta 'banco de manifesta...' nao encontrada na area de trabalho."
  }
  $Source = Join-Path $Banco.FullName "matheusx\Matheus\Matheus\Trabalhos antigos"
}

$ArgsList = @(
  "-m", "app.motor.importar_pasta",
  "--source", $Source,
  "--categoria", $Categoria,
  "--report", $Report
)

if ($Limit -gt 0) {
  $ArgsList += @("--limit", [string]$Limit)
}

if ($DryRun) {
  $ArgsList += "--dry-run"
}

Write-Host "Importando corpus:" $Source
Write-Host "Relatorio:" $Report
& $Python @ArgsList
