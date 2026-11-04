# The installer scripts' console look (setup_models.ps1, setup_prereqs.ps1): dot-sourced after $root and T are defined.
# The studio console's splash (app/console.py) in PowerShell 5.1; tools/install_console.py is the Python twin.
# True colour (VT) where the console takes it; the old conhost without it gets the banner in 16 colours; redirected output and NO_COLOR get plain text.
$esc = [char]27
function Enable-Vt {
    if ($env:NO_COLOR -or $env:TERM -eq 'dumb') { return $false }
    if ($env:FORCE_COLOR) { return $true }   # the convention: colour into a pipe (tests, screenshots)
    if ([Console]::IsOutputRedirected) { return $false }
    try {
        Add-Type -Namespace Rq -Name Con -ErrorAction Stop -MemberDefinition '[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int n); [DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out uint m); [DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint m);'
        $h = [Rq.Con]::GetStdHandle(-11)   # STD_OUTPUT_HANDLE
        [uint32]$mode = 0
        if (-not [Rq.Con]::GetConsoleMode($h, [ref]$mode)) { return $false }
        if (($mode -band 4) -ne 0) { return $true }
        return [Rq.Con]::SetConsoleMode($h, ($mode -bor 5))   # VT processing | processed output
    } catch { return $false }
}
$script:vt = Enable-Vt
$script:tty = -not [Console]::IsOutputRedirected
$cViolet = 168, 85, 247; $cPink = 236, 72, 153; $cGreen = 74, 222, 128; $cRed = 248, 113, 113; $cAmber = 251, 191, 36; $cCyan = 103, 232, 249; $cGrey = 148, 163, 184
# Colours a piece of text (returns it plain without VT)
function C([int[]]$Rgb, [string]$Text, [switch]$Bold) {
    if (-not $script:vt) { return $Text }
    $b = if ($Bold) { '1;' } else { '' }
    "$esc[${b}38;2;$($Rgb[0]);$($Rgb[1]);$($Rgb[2])m$Text$esc[0m"
}
function Dim([string]$Text) { if ($script:vt) { "$esc[2m$Text$esc[0m" } else { $Text } }
function Gb([double]$Bytes) { ($Bytes / 1e9).ToString('0.0', [Globalization.CultureInfo]::InvariantCulture) + ' ' + (T 'ГБ' 'GB') }
function Get-FreeBytes([string]$Path) {
    try { (New-Object IO.DriveInfo ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path)))).AvailableFreeSpace } catch { -1 }
}
function Step([string]$Text) { Write-Host ''; Write-Host ("  " + (C $cViolet '▍' -Bold) + ' ' + $Text) }
function Ok([string]$Text) { Write-Host ("  " + (C $cGreen '✓') + ' ' + $Text) }
function Warn([string]$Text) { Write-Host ("  " + (C $cAmber '!' -Bold) + ' ' + (C $cAmber $Text)) }
# One question with its default in brackets; Enter takes the default, no input at all (a closed stdin) returns $null
function Ask([string]$Prompt, [string]$Default = '') {
    $d = if ($Default) { ' ' + (Dim "[$Default]") } else { '' }
    Write-Host ("  " + (C $cViolet '?' -Bold) + " $Prompt$d : ") -NoNewline
    $a = Read-Host
    if ($null -eq $a) { return $null }
    if ($a.Trim() -eq '') { return $Default }
    return $a.Trim()
}

# «REMIQORA» in ANSI Shadow, the same letters as app/console.py and tools/install_console.py
$logo = @(
    '██████╗ ███████╗███╗   ███╗██╗ ██████╗  ██████╗ ██████╗  █████╗ ',
    '██╔══██╗██╔════╝████╗ ████║██║██╔═══██╗██╔═══██╗██╔══██╗██╔══██╗',
    '██████╔╝█████╗  ██╔████╔██║██║██║   ██║██║   ██║██████╔╝███████║',
    '██╔══██╗██╔══╝  ██║╚██╔╝██║██║██║▄▄ ██║██║   ██║██╔══██╗██╔══██║',
    '██║  ██║███████╗██║ ╚═╝ ██║██║╚██████╔╝╚██████╔╝██║  ██║██║  ██║',
    '╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝╚═╝ ╚══▀▀═╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝'
)
function Get-AppVersion {
    if ($env:REMIQORA_VIDEO_VERSION) { return $env:REMIQORA_VIDEO_VERSION }   # the desktop app's own version
    try { (Get-Content (Join-Path $root 'desktop\package.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version } catch { '' }
}
function Show-Banner([string]$Subtitle) {
    Write-Host ''
    if ($script:vt) {
        $g0 = 0x93, 0x33, 0xea; $g1 = 0xdb, 0x27, 0x77   # studio.css --grad: violet -> pink
        foreach ($row in $logo) {
            $n = [Math]::Max($row.Length - 1, 1)
            $sb = New-Object System.Text.StringBuilder
            [void]$sb.Append('  ')
            for ($x = 0; $x -lt $row.Length; $x++) {
                $ch = $row[$x]
                if ($ch -eq ' ') { [void]$sb.Append($ch); continue }
                $t = $x / $n
                $k = if ('█▄▀'.Contains([string]$ch)) { 1.0 } else { 0.55 }   # the shadow's lines: the same hue, darker
                $r = [int][Math]::Round(($g0[0] + ($g1[0] - $g0[0]) * $t) * $k)
                $gg = [int][Math]::Round(($g0[1] + ($g1[1] - $g0[1]) * $t) * $k)
                $b = [int][Math]::Round(($g0[2] + ($g1[2] - $g0[2]) * $t) * $k)
                [void]$sb.Append("$esc[38;2;${r};${gg};${b}m$ch")
            }
            [void]$sb.Append("$esc[0m")
            Write-Host $sb.ToString()
        }
        Write-Host ('  ' + (C $g1 'V I D E O' -Bold) + (Dim '  ·  ') + $Subtitle)
        Write-Host ('  ' + (Dim ('─' * $logo[0].Length)))
    } elseif ($script:tty) {
        # no true colour (the old conhost): 16 colours, violet to pink across the letters
        foreach ($row in $logo) {
            Write-Host '  ' -NoNewline
            for ($x = 0; $x -lt $row.Length; $x++) {
                $col = if ($row[$x] -notin '█', '▄', '▀') { 'DarkMagenta' } elseif ($x -lt $row.Length * 2 / 3) { 'Magenta' } else { 'Red' }
                Write-Host $row[$x] -NoNewline -ForegroundColor $col
            }
            Write-Host ''
        }
        Write-Host ('  V I D E O  ·  ' + $Subtitle) -ForegroundColor Magenta
        Write-Host ('  ' + ('─' * $logo[0].Length)) -ForegroundColor DarkGray
    } else {
        Write-Host ('  REMIQORA VIDEO  ·  ' + $Subtitle)
        Write-Host ('  ' + ('-' * 40))
    }
    Write-Host ''
}
# The window of a double-clicked .bat (REMIQORA_WAIT, set by the .bat) stays up until Enter
function Wait-Close {
    if (-not $env:REMIQORA_WAIT) { return }
    Write-Host ''
    Write-Host ('  ' + (Dim (T 'Нажмите Enter, чтобы закрыть' 'Press Enter to close')))
    try { [void](Read-Host) } catch { }
}
