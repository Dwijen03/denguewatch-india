# Prints the path to Anaconda/Miniconda's activate.bat, wherever it is installed.
# Used by setup_windows.bat and start_app.bat.

$cands = New-Object System.Collections.Generic.List[string]

# 1. Environment variable set by conda
if ($env:CONDA_EXE) { $cands.Add((Split-Path (Split-Path $env:CONDA_EXE))) }

# 2. The "Anaconda Prompt" Start-menu shortcut (its arguments contain the install path)
$menus = @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
           "$env:ProgramData\Microsoft\Windows\Start Menu\Programs")
try {
    $shell = New-Object -ComObject WScript.Shell
    Get-ChildItem -Path $menus -Recurse -Filter "*Prompt*.lnk" -ErrorAction SilentlyContinue | ForEach-Object {
        $lnk = $shell.CreateShortcut($_.FullName)
        $text = "$($lnk.Arguments) $($lnk.TargetPath)"
        if ($text -match '([A-Za-z]:\\[^"]*?)\\Scripts\\activate\.bat') { $cands.Add($Matches[1]) }
    }
} catch {}

# 3. Registry entries written by the Anaconda installer
foreach ($root in @('HKCU:\Software\Python\ContinuumAnalytics', 'HKLM:\Software\Python\ContinuumAnalytics')) {
    Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
        $p = (Get-ItemProperty -Path (Join-Path $_.PSPath 'InstallPath') -ErrorAction SilentlyContinue).'(default)'
        if ($p) { $cands.Add($p) }
    }
}

# 4. Common install folders on every drive
foreach ($drive in (Get-PSDrive -PSProvider FileSystem).Root) {
    foreach ($name in @('anaconda3', 'Anaconda3', 'miniconda3', 'Miniconda3', 'Anaconda', 'ProgramData\anaconda3', 'ProgramData\miniconda3')) {
        $cands.Add((Join-Path $drive $name))
    }
}
foreach ($name in @('anaconda3', 'miniconda3', 'Anaconda3')) {
    $cands.Add((Join-Path $env:USERPROFILE $name))
    $cands.Add((Join-Path $env:LOCALAPPDATA $name))
}

foreach ($c in $cands) {
    if ($c -and (Test-Path (Join-Path $c 'Scripts\activate.bat'))) {
        Write-Output (Join-Path $c 'Scripts\activate.bat')
        exit 0
    }
}
exit 1
