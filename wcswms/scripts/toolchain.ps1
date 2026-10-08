# Resolve the project's build tools without modifying machine/user PATH.
$bundledRoot = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies'
foreach ($entry in @(@('node', 'node/bin'), @('pnpm', 'bin/fallback'), @('git', 'native/git/cmd'))) {
    if (-not (Get-Command $entry[0] -ErrorAction SilentlyContinue)) {
        $candidate = Join-Path $bundledRoot $entry[1]
        if (Test-Path -LiteralPath $candidate) { $env:PATH = $candidate + [IO.Path]::PathSeparator + $env:PATH }
    }
}
$env:PYTHONUTF8 = '1'
