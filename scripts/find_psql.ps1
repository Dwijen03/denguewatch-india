# Prints the full path to psql.exe, wherever PostgreSQL is installed.
# Used by setup_windows.bat and fix_database.bat.

$cands = New-Object System.Collections.Generic.List[string]

# 1. The PostgreSQL Windows service knows exactly where it is installed
try {
    Get-CimInstance Win32_Service -ErrorAction Stop |
        Where-Object { $_.Name -like 'postgresql*' -or $_.PathName -like '*pg_ctl*' } |
        ForEach-Object {
            if ($_.PathName -match '^"?([^"]*?)\\pg_ctl\.exe') { $cands.Add((Join-Path $Matches[1] 'psql.exe')) }
        }
} catch {}

# 2. psql already on PATH
$onPath = Get-Command psql.exe -ErrorAction SilentlyContinue
if ($onPath) { $cands.Add($onPath.Source) }

# 3. Registry entries written by the EDB installer
foreach ($root in @('HKLM:\SOFTWARE\PostgreSQL\Installations', 'HKLM:\SOFTWARE\WOW6432Node\PostgreSQL\Installations')) {
    Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
        $base = (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).'Base Directory'
        if ($base) { $cands.Add((Join-Path $base 'bin\psql.exe')) }
    }
}

# 4. Common folders on every drive
foreach ($drive in (Get-PSDrive -PSProvider FileSystem).Root) {
    foreach ($sub in @('Program Files\PostgreSQL', 'PostgreSQL', 'Program Files (x86)\PostgreSQL')) {
        $dir = Join-Path $drive $sub
        if (Test-Path $dir) {
            Get-ChildItem $dir -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | ForEach-Object {
                $cands.Add((Join-Path $_.FullName 'bin\psql.exe'))
            }
        }
    }
}

foreach ($c in $cands) {
    if ($c -and (Test-Path $c)) { Write-Output $c; exit 0 }
}
exit 1
