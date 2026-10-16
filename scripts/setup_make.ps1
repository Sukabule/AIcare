param([string]$Proxy = '')
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$taskBin = Join-Path $taskRoot '.cache/bin'
$taskPackage = Join-Path $taskRoot '.cache/make-package'
New-Item -ItemType Directory -Path $taskBin,$taskPackage -Force | Out-Null
if (Test-Path -LiteralPath (Join-Path $taskBin 'make.exe')) {
    & (Join-Path $taskBin 'make.exe') --version
    exit $LASTEXITCODE
}
# Official MSYS2 mingw64 package, pinned with its published SHA256.
$taskUrl = 'https://mirror.msys2.org/mingw/mingw64/mingw-w64-x86_64-make-4.4.1-5-any.pkg.tar.zst'
$taskSha = 'c19e7caf09bbc89b2730556b2da73004118d4e1f967a685cc48524c7ff80d864'
$taskArchive = Join-Path $taskPackage 'make.pkg.tar.zst'
$taskRequest = @{Uri=$taskUrl; OutFile=$taskArchive}
if (-not $Proxy) {
    $taskProxySettings = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
    if ($taskProxySettings.ProxyEnable -eq 1 -and $taskProxySettings.ProxyServer -match '^127\.0\.0\.1:\d+$') {
        $Proxy = 'http://' + $taskProxySettings.ProxyServer
    }
}
if ($Proxy) { $taskRequest.Proxy = $Proxy }
Invoke-WebRequest @taskRequest
if ((Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskSha) {
    throw '官方 make 包校验失败，未执行下载的程序。'
}
tar.exe -xf $taskArchive -C $taskPackage 'mingw64/bin/mingw32-make.exe' 'mingw64/share/licenses/make/COPYING'
if ($LASTEXITCODE -ne 0) { throw '解包失败，需要支持 zstd 的 Windows tar。' }
Copy-Item -LiteralPath (Join-Path $taskPackage 'mingw64/bin/mingw32-make.exe') -Destination (Join-Path $taskBin 'make.exe')
Copy-Item -LiteralPath (Join-Path $taskPackage 'mingw64/share/licenses/make/COPYING') -Destination (Join-Path $taskBin 'MAKE-LICENSE.txt')
$taskGit = Get-Command git -ErrorAction Stop
$taskGitRoot = Split-Path (Split-Path $taskGit.Source -Parent) -Parent
foreach ($taskDll in @('libintl-8.dll','libiconv-2.dll','libwinpthread-1.dll','libgcc_s_seh-1.dll')) {
    $taskDllPath = Join-Path $taskGitRoot "mingw64/bin/$taskDll"
    if (Test-Path -LiteralPath $taskDllPath) { Copy-Item -LiteralPath $taskDllPath -Destination $taskBin }
}
& (Join-Path $taskBin 'make.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'make 启动失败，请检查 Git for Windows 的运行库。' }
Write-Host 'make 已放在项目 .cache/bin，不改系统 PATH。dev.ps1 和后台作业会自动使用。'
