# Run a command and report wall time plus peak memory of its whole process tree (the process and all children,
# e.g. ans16 worker processes), sampled every -IntervalMs.
# Usage: pwsh -File measure.ps1 -Exe <program> -Arguments <arg1>,<arg2>,... [-IntervalMs 500]
param(
    [Parameter(Mandatory)] [string]$Exe,
    [string[]]$Arguments = @(),
    [int]$IntervalMs = 500
)
$quoted = $Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }
$sw = [Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $Exe -ArgumentList $quoted -NoNewWindow -PassThru
$root = $proc.Id
$peakTree = 0; $peakTreePrivate = 0; $peakProcs = 0
while (-not $proc.HasExited) {
    $all = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, WorkingSetSize, PrivatePageCount, CreationDate
    $children = @{}
    foreach ($p in $all) { $children[[int]$p.ParentProcessId] += @($p) }
    $tree = @(); $queue = [System.Collections.Generic.Queue[object]]::new()
    $self = $all | Where-Object { $_.ProcessId -eq $root }
    if ($self) { $tree += $self; $queue.Enqueue($self) }
    while ($queue.Count) {
        $parent = $queue.Dequeue()
        foreach ($c in $children[[int]$parent.ProcessId]) {
            # Windows reuses PIDs: a "child" created before its parent belongs to a dead process that had this PID.
            if ($c -and $c.CreationDate -ge $parent.CreationDate) { $tree += $c; $queue.Enqueue($c) }
        }
    }
    $ws = ($tree | Measure-Object WorkingSetSize -Sum).Sum
    $priv = ($tree | Measure-Object PrivatePageCount -Sum).Sum
    if ($ws -gt $peakTree) { $peakTree = $ws }
    if ($priv -gt $peakTreePrivate) { $peakTreePrivate = $priv }
    if ($tree.Count -gt $peakProcs) {
        $peakProcs = $tree.Count
        $ids = $tree | ForEach-Object { $_.ProcessId }
        $peakNames = (Get-Process -Id $ids -ErrorAction SilentlyContinue | Group-Object ProcessName |
            ForEach-Object { "$($_.Name) x$($_.Count)" }) -join ', '
    }
    Start-Sleep -Milliseconds $IntervalMs
}
$proc.WaitForExit()
$sw.Stop()
"MEASURE exit={0} wall={1:N1}s peak_tree_working_set={2:N2}GiB peak_tree_private={3:N2}GiB peak_processes={4}" -f `
    $proc.ExitCode, $sw.Elapsed.TotalSeconds, ($peakTree / 1GB), ($peakTreePrivate / 1GB), $peakProcs
"MEASURE processes at peak count: $peakNames"
exit $proc.ExitCode
