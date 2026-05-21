[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Tag,

    [string]$CommitMessage,

    [switch]$Push
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if ($Tag -notmatch '^kai-v\d{4}-\d{2}-\d{2}(-[A-Za-z0-9._-]+)?$') {
    throw "Tag must look like kai-vYYYY-MM-DD or kai-vYYYY-MM-DD-name"
}

$Dirty = git status --porcelain

if ($CommitMessage) {
    git add -A
    $DirtyAfterAdd = git status --porcelain
    if ($DirtyAfterAdd) {
        git commit -m $CommitMessage
    }
    else {
        Write-Host "No changes to commit."
    }
}
elseif ($Dirty) {
    throw "Working tree has uncommitted changes. Commit first or pass -CommitMessage."
}

& "$PSScriptRoot\verify_local.ps1"

$ExistingTag = git tag --list $Tag
if ($ExistingTag) {
    throw "Tag already exists: $Tag"
}

git tag -a $Tag -m "Release $Tag"
Write-Host "Created release tag $Tag"

if ($Push) {
    git push origin main
    git push origin $Tag
    Write-Host "Pushed main and $Tag to origin."
}
else {
    Write-Host "Push skipped. To publish: git push origin main --tags"
}
