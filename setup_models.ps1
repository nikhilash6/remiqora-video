#Requires -Version 5.1
param(
    [string]$ModelsDir,
    [string[]]$Find,
    [string]$Sets,
    [Alias('Profile')][ValidateSet('', 'minimum', 'h3', 'faces', 'all')][string]$SetProfile = '',   # not $Profile: PowerShell's own variable
    [switch]$AcceptH3,   # accept MiniMax H3's licence statement without asking (unattended runs)
    [switch]$SkipWeights,
    [switch]$SkipEngine,
    [switch]$SkipApp,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

# Message language: REMIQORA_VIDEO_LANG if set (the desktop wizard does not run these scripts), else the Windows display language; Russian or English
$lang = if ($env:REMIQORA_VIDEO_LANG) { $env:REMIQORA_VIDEO_LANG } else { (Get-UICulture).TwoLetterISOLanguageName }
$script:ru = $lang.ToLowerInvariant().StartsWith('ru')
function T([string]$Ru, [string]$En) { if ($script:ru) { $Ru } else { $En } }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$engine = Join-Path $root 'engine'

. (Join-Path $root 'tools\install_console.ps1')   # colours, the banner, Ask / Step / Ok helpers
try { $Host.UI.RawUI.WindowTitle = T 'Remiqora Video — установка' 'Remiqora Video — install' } catch { }
$appVersion = Get-AppVersion
$subtitle = T 'Установка моделей' 'Model installer'
if ($appVersion) { $subtitle += (T '  ·  версия ' '  ·  version ') + $appVersion }
Show-Banner $subtitle

# A failure ends in one clear red line (the window stays up: install.bat waits for Enter when it was started by a double click)
trap {
    Write-Host ''
    Write-Host ("  " + (C $cRed '✗' -Bold) + ' ' + (C $cRed (T 'Установка остановилась: ' 'The install stopped: ')) + $_.Exception.Message)
    Write-Host ("  " + (Dim (T 'Запустите её ещё раз: уже скачанное не качается заново, прерванные файлы продолжатся с того же места.' 'Run it again: what is downloaded stays, interrupted files continue where they stopped.')))
    Wait-Close
    exit 1
}

# winget writes new PATH entries to the registry; refresh them in this PowerShell process so a GUI
# installer can install prerequisites and then continue without asking the user to reopen a terminal.
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$env:Path = @($env:Path, $machinePath, $userPath) -join ';'

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { throw (T "Не найдено '$Name'. $Hint" "'$Name' not found. $Hint") }
}
function Invoke-Native([string]$Name, [string[]]$NativeArgs) {
    & $Name @NativeArgs
    if ($LASTEXITCODE -ne 0) { throw (T "Команда завершилась с кодом $LASTEXITCODE`: $Name $($NativeArgs -join ' ')" "Command exited with code $LASTEXITCODE`: $Name $($NativeArgs -join ' ')") }
}
function Invoke-Native-Retry([string]$Name, [string[]]$NativeArgs, [int]$Attempts = 5) {
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        & $Name @NativeArgs
        if ($LASTEXITCODE -eq 0) { return }
        if ($attempt -lt $Attempts) {
            $delay = [Math]::Min(30, 3 * [Math]::Pow(2, $attempt - 1))
            Write-Host (T "Сетевой запрос не завершился (код $LASTEXITCODE). Повтор $attempt из $($Attempts - 1) через $([int]$delay) сек…" "Network request failed (code $LASTEXITCODE). Retry $attempt of $($Attempts - 1) in $([int]$delay) s…") -ForegroundColor Yellow
            Start-Sleep -Seconds ([int]$delay)
        }
    }
    throw (T "Команда не удалась после $Attempts попыток (код $LASTEXITCODE): $Name $($NativeArgs -join ' ')" "Command failed after $Attempts attempts (code $LASTEXITCODE): $Name $($NativeArgs -join ' ')")
}
function Resolve-ModelRoots {
    param([string]$DefaultPath, [string[]]$SearchRoots)
    if (-not $DefaultPath) { $DefaultPath = Join-Path $env:LOCALAPPDATA 'RemiqoraVideo\models' }
    Step (T 'Папка для моделей' 'Models folder')
    Write-Host (Dim (T '  Можно выбрать существующую папку ComfyUI/Comfy Desktop с подпапками моделей: найденные веса останутся на месте.' '  You can pick an existing ComfyUI/Comfy Desktop folder with model subfolders: the weights found stay where they are.'))
    Write-Host ('  ' + (T 'По умолчанию:' 'Default:') + ' ' + (C $cCyan $DefaultPath))
    $free = Get-FreeBytes $DefaultPath
    if ($free -ge 0) { Write-Host ('  ' + (T 'Свободно на диске:' 'Free on that disk:') + ' ' + (C $cGreen (Gb $free))) }
    if (-not $ModelsDir -and -not $DryRun) {
        $answer = Ask (T 'Путь, B — выбрать папку в окне, Enter — по умолчанию' 'A path, B to pick a folder in a window, Enter for the default')
        if ($null -eq $answer) { $answer = '' }
        if ($answer.Trim().ToUpperInvariant() -eq 'B') {
            Add-Type -AssemblyName System.Windows.Forms
            $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
            $dialog.Description = T 'Выберите папку models (или общую папку с подпапкой models)' 'Choose the models folder (or a parent folder that has a models subfolder)'
            $dialog.ShowNewFolderButton = $true
            $dialog.SelectedPath = $DefaultPath
            if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $DefaultPath = $dialog.SelectedPath }
        } elseif ($answer.Trim()) { $DefaultPath = $answer.Trim().Trim('"') }
    }
    $roots = @($DefaultPath)
    if ($SearchRoots) { $roots += $SearchRoots }
    $roots = @($roots | ForEach-Object {
        $candidate = [IO.Path]::GetFullPath($_)
        foreach ($nested in @((Join-Path $candidate 'models'), (Join-Path $candidate 'ComfyUI\models'))) {
            if (-not (Test-Path (Join-Path $candidate 'diffusion_models')) -and (Test-Path (Join-Path $nested 'diffusion_models'))) { $candidate = $nested; break }
        }
        $candidate
    } | Select-Object -Unique)
    foreach ($path in $roots) {
        if (-not (Test-Path -LiteralPath $path)) {
            if ($path -eq $DefaultPath) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
            else { throw (T "Указанная папка моделей не существует: $path" "The models folder does not exist: $path") }
        }
    }
    $found = foreach ($path in $roots) {
        Get-ChildItem -LiteralPath $path -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in '.safetensors','.gguf','.ckpt','.pt','.bin' } |
            Select-Object -First 20 -ExpandProperty FullName
    }
    if ($found) { Write-Host (T 'Найдены существующие веса. Они останутся на месте и будут подключены:' 'Existing weights found. They stay where they are and will be connected:'); $found | Select-Object -First 8 | ForEach-Object { Write-Host "  $_" } }
    else { Write-Host ('  ' + (C $cAmber (T 'В выбранной папке пока нет файлов моделей. Её можно оставить пустой и скачать веса позже со страницы «Модели».' 'No model files in the chosen folder yet. You can leave it empty and download the weights later from the Models page.'))) }
    return $roots
}
function Set-DotEnvValue([string]$Path, [string]$Key, [string]$Value) {
    # .env is UTF-8 without BOM (python-dotenv reads it so); Get-Content/Set-Content in PowerShell 5.1 would read it as
    # the ANSI codepage and write a BOM, turning the comments' «» into mojibake
    $lines = @(if (Test-Path -LiteralPath $Path) { [IO.File]::ReadAllLines($Path, $utf8) })
    $prefix = "$Key="
    $replacement = "$Key=$Value"
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line.StartsWith($prefix)) { $found = $true; $replacement } else { $line }
    }
    if (-not $found) { $out += $replacement }
    [IO.File]::WriteAllLines($Path, [string[]]$out, $utf8)
}

$modelRoots = @(Resolve-ModelRoots -DefaultPath $ModelsDir -SearchRoots $Find)
if ($DryRun) {
    Write-Host (T "`nПроверка пройдена: папки моделей будут подключены через H3_MODEL_ROOTS=$($modelRoots -join ';')" "`nCheck passed: the model folders will be connected through H3_MODEL_ROOTS=$($modelRoots -join ';')")
    exit 0
}

$python = $null
$needPython = (-not $SkipEngine) -or ((-not $SkipApp) -and -not (Test-Path (Join-Path $root '.venv\Scripts\python.exe')))
if ($needPython) {
    if (Get-Command py -ErrorAction SilentlyContinue) { $python = (& py -3.13 -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1) }
    if (-not $python -or -not (Test-Path -LiteralPath $python)) { throw (T 'Нужен Python 3.13. Запустите setup_prereqs.bat, затем откройте новое окно.' 'Python 3.13 is required. Run setup_prereqs.bat, then open a new window.') }
}

$envFile = Join-Path $root '.env'
if (-not (Test-Path -LiteralPath $envFile)) { Copy-Item (Join-Path $root '.env.example') $envFile }
Set-DotEnvValue $envFile 'H3_MODEL_ROOTS' ($modelRoots -join ';')
$primary = $modelRoots[0]
Set-DotEnvValue $envFile 'H3_REF_DIR' $primary
Set-DotEnvValue $envFile 'LLM_DIRS' (Join-Path $primary 'llm')
Set-DotEnvValue $envFile 'LORA_DIRS' (Join-Path $primary 'loras\h3')
Set-DotEnvValue $envFile 'LTX23_LORA_DIRS' (Join-Path $primary 'loras\ltx23')
Set-DotEnvValue $envFile 'LTX25_LORA_DIRS' (Join-Path $primary 'loras\ltx25')
Set-DotEnvValue $envFile 'WAN_LORA_DIRS' (Join-Path $primary 'loras\wan5b')
Set-DotEnvValue $envFile 'KREA_LORA_DIRS' (Join-Path $primary 'loras\krea2')
foreach ($folder in @('diffusion_models','text_encoders','vae','loras','checkpoints','latent_upscale_models','llm','loras\h3','loras\ltx23','loras\ltx25','loras\wan5b','loras\krea2')) {
    New-Item -ItemType Directory -Path (Join-Path $primary $folder) -Force | Out-Null
}

if (-not $SkipEngine) {
    Step (T 'Движок ComfyUI (закреплённая версия) и его окружение' 'ComfyUI engine (the pinned version) and its environment')
    Require-Command 'git' (T 'Запустите setup_prereqs.bat.' 'Run setup_prereqs.bat.')
    Require-Command 'uv' (T 'Запустите setup_prereqs.bat.' 'Run setup_prereqs.bat.')
    $coreInfo = @(Get-Content (Join-Path $engine 'core.txt') | Where-Object { $_.Trim() })
    if ($coreInfo.Count -lt 2) { throw (T 'engine\core.txt должен содержать URL репозитория и закреплённый commit SHA.' 'engine\core.txt must hold the repository URL and the pinned commit SHA.') }
    $repoUrl, $sha = $coreInfo[0].Trim(), $coreInfo[1].Trim()
    $comfy = Join-Path $engine 'ComfyUI'
    if (-not (Test-Path (Join-Path $comfy '.git'))) { New-Item -ItemType Directory -Path $comfy -Force | Out-Null; Invoke-Native git @('-C',$comfy,'init','-q'); Invoke-Native git @('-C',$comfy,'remote','add','origin',$repoUrl) }
    $gitLock = Join-Path $comfy '.git\index.lock'
    if (Test-Path -LiteralPath $gitLock) {
        $activeGit = @(Get-Process -Name git -ErrorAction SilentlyContinue)
        if ($activeGit.Count -gt 0) { throw (T 'Git ещё работает. Дождитесь завершения другого процесса установки и повторите запуск.' 'Git is still running. Wait for the other install process to finish and run this again.') }
        Write-Host (T 'Удаляю оставшийся lock-файл Git после прерванной установки.' 'Removing the Git lock file left by an interrupted install.') -ForegroundColor Yellow
        Remove-Item -LiteralPath $gitLock -Force
    }
    # Fetch advertised refs rather than a raw SHA (GitHub may reject an unadvertised commit fetch).
    Invoke-Native-Retry git @('-C',$comfy,'fetch','-q','--filter=blob:none','origin')
    Invoke-Native git @('-C',$comfy,'-c','advice.detachedHead=false','checkout','-q','--force',$sha)
    $env:UV_PYTHON = $python
    $env:UV_PYTHON_DOWNLOADS = 'never'
    $env:UV_LINK_MODE = 'copy'
    Invoke-Native uv @('sync','--project',$engine,'--frozen')
    $enginePython = Join-Path $engine '.venv\Scripts\python.exe'
    Invoke-Native $enginePython @('-c',"import torch; assert torch.cuda.is_available(), 'CUDA is unavailable or the NVIDIA driver is too old'; print('torch', torch.__version__, '-', torch.cuda.get_device_name(0))")
    Ok (T 'Движок готов' 'The engine is ready')
}

if (-not $SkipApp -and -not (Test-Path (Join-Path $root '.venv\Scripts\python.exe'))) {
    Step (T 'Окружение приложения' 'The app environment')
    Invoke-Native $python @('-m','venv',(Join-Path $root '.venv'))
    Invoke-Native (Join-Path $root '.venv\Scripts\python.exe') @('-m','pip','install','--upgrade','pip')
    Invoke-Native (Join-Path $root '.venv\Scripts\pip.exe') @('install','-r',(Join-Path $root 'requirements.txt'))
    Ok (T 'Приложение готово' 'The app is ready')
}

# The model sets: the official weights of install\manifest.json, the same downloader the desktop wizard runs
$appPython = Join-Path $root '.venv\Scripts\python.exe'
$weightsCode = 0
$declined = $false   # «n» to «Продолжить?»: the app is in, the models are not
if (-not $SkipWeights -and (Test-Path $appPython)) {
    Step (T 'Модели' 'Models')
    $manifest = Get-Content (Join-Path $root 'install\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $interactive = $false
    if (-not $Sets -and -not $SetProfile) {
        $interactive = $true
        # a profile's size: its sets from the manifest (a set that needs another already lists it)
        $profileBytes = @{}
        foreach ($p in 'minimum', 'h3', 'faces', 'all') { $profileBytes[$p] = [double](@($manifest.profiles.$p | ForEach-Object { $manifest.sets.$_.bytes }) | Measure-Object -Sum).Sum }
        Write-Host (Dim (T '  Только официальные веса; свои файлы подключаются на странице «Модели».' '  Official weights only; your own files are connected on the Models page.'))
        Write-Host ''
        $menu = @(
            @('1', (T 'Минимум' 'Minimum'), (T 'сценарист + LTX 2.3 (без H3)' 'the writer + LTX 2.3 (no H3)'), $profileBytes['minimum']),
            @('2', 'H3', (T 'H3 + сценарист' 'H3 + the writer'), $profileBytes['h3']),
            @('3', (T 'H3 + лица' 'H3 + faces'), (T 'ещё Krea 2, FLUX.2 Klein и лица по фото (Ref2VA)' 'plus Krea 2, FLUX.2 Klein and faces from photos (Ref2VA)'), $profileBytes['faces']),
            @('4', (T 'Всё' 'Everything'), (T 'ещё LTX 2.5, LTX 2.3 и WAN 2.2 5B' 'plus LTX 2.5, LTX 2.3 and WAN 2.2 5B'), $profileBytes['all'])
        )
        foreach ($m in $menu) {
            Write-Host ('  ' + (C $cViolet $m[0] -Bold) + '  ' + (C $cPink $m[1].PadRight(16) -Bold) + $m[2].PadRight(58) + (C $cCyan ('~' + (Gb $m[3])) -Bold))
        }
        Write-Host ('  ' + (C $cViolet '5' -Bold) + '  ' + (C $cGrey ((T 'Ничего сейчас' 'Nothing now').PadRight(16))) + (T 'скачать позже на странице «Модели»' 'download later on the Models page'))
        Write-Host ''
        $answer = Ask (T 'Номер' 'Number') '1'
        $SetProfile = ''
        if ($null -ne $answer) { $SetProfile = @{ '1' = 'minimum'; '2' = 'h3'; '3' = 'faces'; '4' = 'all' }[$answer] }
        if (-not $SetProfile) { Write-Host (Dim (T '  Модели сейчас не скачиваются.' '  No models now.')) }
    }
    # MiniMax H3's licence statement: H3 is not installed without an explicit yes (or -AcceptH3 for unattended runs)
    $wantsH3 = ($Sets -match '(^|,)\s*(h3|ref2va)\s*(,|$)') -or ($SetProfile -and $SetProfile -ne 'minimum')
    if ($wantsH3 -and -not $AcceptH3) {
        Write-Host ''
        Write-Host ('  ' + (C $cAmber (T 'Лицензия MiniMax H3' 'MiniMax H3 licence') -Bold))
        Write-Host (T 'Лицензия MiniMax H3 не действует в США, ЕС, Великобритании и Южной Корее. Устанавливая H3, вы сами отвечаете за соблюдение её условий; автор Remiqora Video ответственности не несёт.' 'The MiniMax H3 license does not apply in the United States, the EU, the United Kingdom and South Korea. By installing H3 you are responsible for complying with its terms; the author of Remiqora Video bears no responsibility.')
        Write-Host (T 'Текст лицензии: https://huggingface.co/MiniMaxAI/MiniMax-H3' 'License text: https://huggingface.co/MiniMaxAI/MiniMax-H3')
        if ((Ask (T 'Я прочитал(а) и принимаю? (y/N)' 'I have read and accept? (y/N)') 'N') -match '^[yYдД]') { $AcceptH3 = $true }
        else {
            # without the yes only H3's own sets (h3, ref2va) are dropped; the rest of the choice still downloads
            $chosen = if ($Sets) { @($Sets -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) } else { @($manifest.profiles.$SetProfile) }
            $kept = @($chosen | Where-Object { $_ -notin @('h3', 'ref2va') })
            $Sets = $kept -join ','; $SetProfile = ''
            if ($kept) { Write-Host (T "H3 не ставится (нет согласия). Остальное скачается: $Sets" "H3 is not installed (not accepted). The rest is downloaded: $Sets") }
            else { Write-Host (T 'H3 не ставится: без согласия набор пропущен, скачивать нечего.' 'H3 is not installed: skipped without acceptance, nothing else to download.') }
        }
    }
    if ($Sets -or $SetProfile) {
        $pick = if ($Sets) { @('--sets', $Sets) } else { @('--profile', $SetProfile) }
        $dl = @('--lang', (T 'ru' 'en'), '--models-dir', $primary)
        $findArg = @($modelRoots | Select-Object -Skip 1) -join ';'
        if ($findArg) { $dl += @('--find', $findArg) }
        $dl += $pick
        if ($AcceptH3) { $dl += '--accept-h3' }
        $script = Join-Path $root 'tools\install_models.py'

        # What is there already and what comes: the downloader's own plan (it knows the files found in the model folders)
        $total = -1; $planSets = $null
        try {
            $planEvent = @(& $appPython $script @dl --json --plan | ForEach-Object { try { $_ | ConvertFrom-Json } catch { } } | Where-Object { $_.event -eq 'plan' }) | Select-Object -Last 1
            if ($planEvent) { $total = [double]$planEvent.total; $planSets = $planEvent.sets }
        } catch { }
        $proceed = $true
        if ($total -ge 0) {
            $free = Get-FreeBytes $primary
            Write-Host ''
            foreach ($prop in $planSets.PSObject.Properties) {
                $s = $planSets.($prop.Name)
                $need = $s.bytes - $s.have
                $state = if ($need -le 0) { C $cGreen (T 'уже есть' 'already there') } else { (C $cCyan (Gb $need)) + (Dim ('  (' + (T 'из ' 'of ') + (Gb $s.bytes) + ')')) }
                $title = if ([string]$s.title -eq 'Writer') { T 'Сценарист' 'Writer' } else { [string]$s.title }
                Write-Host ('  ' + (C $cViolet '▍' -Bold) + ' ' + $title.PadRight(16) + $state)
            }
            Write-Host ''
            Write-Host ('  ' + (Dim (T 'Папка моделей:' 'Models folder:')) + " $primary")
            $enough = ($free -lt 0) -or ($free -ge $total + 512MB)
            $freeText = if ($free -ge 0) { ', ' + (T 'свободно: ' 'free: ') + (C $(if ($enough) { $cGreen } else { $cRed }) (Gb $free) -Bold) } else { '' }
            Write-Host ('  ' + (T 'Будет скачано: ' 'To download: ') + (C $cCyan (Gb $total) -Bold) + $freeText)
            if (-not $enough) { Write-Host ('  ' + (C $cRed (T 'Места не хватает: освободите диск или выберите меньший набор.' 'Not enough space: free some disk or pick a smaller set.') -Bold)) }
            if ($interactive -and $total -gt 0) {
                $go = Ask (T 'Продолжить? (Y/n)' 'Continue? (Y/n)') $(if ($enough) { 'Y' } else { 'N' })
                $proceed = ($null -ne $go) -and ($go -match '^[yYдД]')   # a closed stdin is a no, not 50 GB
                if (-not $proceed) { $declined = $true; Write-Host (Dim (T '  Хорошо, ничего не скачиваю. Позже: страница «Модели» или setup_models.bat.' '  Fine, nothing is downloaded. Later: the Models page or setup_models.bat.')) }
            }
        }
        if ($proceed) {
            & $appPython $script @dl --pretty --no-banner
            $weightsCode = $LASTEXITCODE
            if ($weightsCode -eq 2) { Write-Host (T 'Часть наборов не скачалась: запустите setup_models.bat ещё раз, он продолжит с того же места.' 'Some sets did not download: run setup_models.bat again, it resumes where it stopped.') -ForegroundColor Yellow }
            elseif ($weightsCode -ne 0) { throw (T "Загрузка моделей завершилась с кодом $weightsCode" "The model download exited with code $weightsCode") }
        }
    }
}

Write-Host ''
if ($declined) { Write-Host ('  ' + (C $cAmber '•' -Bold) + ' ' + (C $cAmber (T 'Программа установлена, модели не скачаны.' 'The app is installed, no models downloaded.') -Bold)) }
elseif ($weightsCode -eq 0) { Write-Host ('  ' + (C $cGreen '✓' -Bold) + ' ' + (C $cGreen (T 'Установка завершена.' 'Install finished.') -Bold)) }
else { Write-Host ('  ' + (C $cAmber '!' -Bold) + ' ' + (C $cAmber (T 'Установка завершена не полностью.' 'The install is not complete.') -Bold)) }
Write-Host ('  ' + (Dim (T 'Папки моделей подключены:' 'Model folders connected:')) + " $($modelRoots -join ';')")
Write-Host ('  ' + (T 'Запустите ' 'Run ') + (C $cCyan 'dev.bat' -Bold) + (T '. Недостающие веса можно докачать на странице «Модели».' '. Missing weights can be downloaded later on the Models page.'))
Write-Host ''
Wait-Close
