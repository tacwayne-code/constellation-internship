# Run once from an elevated Windows PowerShell, then sign out and sign in.
# Only grants the deployment account access to the installed TIA Openness API.
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$group = 'Siemens TIA Openness'
$members = @(Get-LocalGroupMember -Group $group)
if (-not ($members | Where-Object { $_.Name -eq $account })) {
    Add-LocalGroupMember -Group $group -Member $account
}
Get-LocalGroupMember -Group $group | Select-Object Name, ObjectClass
Write-Output 'Sign out of Windows and sign in again before running the Siemens scripts.'
