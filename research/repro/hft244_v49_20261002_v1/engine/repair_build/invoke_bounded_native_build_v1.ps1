function Invoke-BoundedNativeBuild {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$LogPrefix,
        [ValidateRange(1,600)][int]$TimeoutSeconds=600
    )
    $taskOut=$LogPrefix+'.stdout.log'
    $taskErr=$LogPrefix+'.stderr.log'
    if ((Test-Path -LiteralPath $taskOut) -or (Test-Path -LiteralPath $taskErr)) { throw 'Build log already exists' }
    $taskWatch=[Diagnostics.Stopwatch]::StartNew()
    $taskChild=Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput $taskOut -RedirectStandardError $taskErr
    $taskTimeout=$false
    while (-not $taskChild.HasExited) {
        if ($taskWatch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
            # The retained handle is ONLY the process started above, plus its children.
            $taskChild.Kill($true)
            $taskChild.WaitForExit(5000) | Out-Null
            $taskTimeout=$true
            break
        }
        Start-Sleep -Milliseconds 250
        $taskChild.Refresh()
    }
    if ($taskChild.HasExited) { $taskChild.WaitForExit(1000) | Out-Null }
    $taskChild.Refresh()
    [pscustomobject]@{
        processId=$taskChild.Id; timedOut=$taskTimeout; exited=$taskChild.HasExited
        exitCode=$taskChild.ExitCode; elapsedSeconds=$taskWatch.Elapsed.TotalSeconds
        stdout=$taskOut; stderr=$taskErr
    }
}
