# Registers a per-user scheduled task that runs the ingest jobs every 15 minutes while you're logged on.
# Usage (from the repo root):  powershell -ExecutionPolicy Bypass -File scripts/register_run_due_task.ps1
# Remove:                      Unregister-ScheduledTask -TaskName BettingAggregatorRunDue -Confirm:$false
param([string]$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path)

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Create the venv first: python -m venv .venv; .venv\Scripts\pip install -e .[dev]" }

$action = New-ScheduledTaskAction -Execute $python -Argument "-m app.jobs run-due" -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "BettingAggregatorRunDue" -Action $action -Trigger $trigger `
    -Settings $settings -Description "betting_aggregator: python -m app.jobs run-due" -Force | Out-Null
Write-Host "Registered BettingAggregatorRunDue (every 15 min). Check: Get-ScheduledTaskInfo BettingAggregatorRunDue"
