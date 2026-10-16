param(
    [Parameter(Mandatory=$true)][int]$TaskProcessId,
    [Parameter(Mandatory=$true)][string]$TaskScriptPath,
    [Parameter(Mandatory=$true)][string]$TaskExecutablePath
)
$ErrorActionPreference = 'Stop'
$taskScript = (Resolve-Path -LiteralPath $TaskScriptPath).Path
$taskExecutable = (Resolve-Path -LiteralPath $TaskExecutablePath).Path
$taskOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$TaskProcessId"
if ($null -eq $taskOwner) { exit 0 }
if (-not $taskOwner.CommandLine.Contains($taskScript) -or -not $taskOwner.CommandLine.Contains($taskExecutable)) {
    throw 'Refusing to stop a PID not owned by this MCP launcher'
}
function Stop-VerifiedMcpTree([int]$TaskNodeId) {
    $taskChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId=$TaskNodeId"
    foreach ($taskChild in $taskChildren) {
        if ($taskChild.CommandLine -and $taskChild.CommandLine.Contains($taskScript)) {
            Stop-VerifiedMcpTree $taskChild.ProcessId
        }
    }
    $taskCurrent = Get-CimInstance Win32_Process -Filter "ProcessId=$TaskNodeId"
    if ($taskCurrent -and $taskCurrent.CommandLine.Contains($taskScript)) {
        Stop-Process -Id $TaskNodeId -Force -ErrorAction SilentlyContinue
    }
}
Stop-VerifiedMcpTree $TaskProcessId
