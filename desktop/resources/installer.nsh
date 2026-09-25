; Extra steps for the Windows NSIS installer, read through nsis.include in electron-builder.yml.
;
; An update runs the previous version's uninstaller with --updated; only a real uninstall
; (Apps & features, or the uninstaller run by hand) lacks that flag. Everything that holds
; the user's data therefore sits behind ${ifNot} ${isUpdated}, so an update keeps monitoring
; history, settings and the sign-in.

!macro customUnInstall
  ${ifNot} ${isUpdated}
    ; The sidecar's data directory: RDST_DATA_DIR when set, otherwise %USERPROFILE%\.rdst.
    nsExec::Exec `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\uninstall-cleanup.ps1"`
    Pop $R0
    ${if} $R0 != 0
      DetailPrint "RDST credential/custom-directory cleanup failed: $R0"
      SetErrorLevel 1
    ${endIf}
    ReadEnvStr $R0 "RDST_DATA_DIR"
    ${if} $R0 == ""
      ReadEnvStr $R0 "USERPROFILE"
      ${if} $R0 != ""
        RMDir /r "$R0\.rdst"
      ${endIf}
    ${endIf}

    ; Electron's profile and the updater's download cache. The uninstaller's own
    ; deleteAppDataOnUninstall covers the first two; the rest guards a renamed product.
    RMDir /r "$APPDATA\rdst-desktop"
    RMDir /r "$APPDATA\RDST Desktop"
    RMDir /r "$LOCALAPPDATA\rdst-desktop-updater"

    ; Start at login. Electron names the Run value after the AppUserModelID.
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "com.readyset.rdst-desktop"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "RDST Desktop"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "rdst-desktop"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "com.readyset.rdst-desktop"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "RDST Desktop"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "rdst-desktop"

  ${endIf}
!macroend
