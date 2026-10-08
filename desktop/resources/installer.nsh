; Extra steps for the Windows NSIS installer, read through nsis.include in electron-builder.yml.
;
; An update runs the previous version's uninstaller with --updated; only a real uninstall
; (Apps & features, or the uninstaller run by hand) lacks that flag. Everything that holds
; the user's data therefore sits behind ${ifNot} ${isUpdated}, so an update keeps monitoring
; history, settings and the sign-in.

; Each flavor removes only its own data, so a CL build never touches production's.
!if "${APP_ID}" == "com.readyset.rdst-desktop"
  !define RDST_DATA_DIR_NAME ".rdst"
  !define RDST_ELECTRON_NAME "rdst-desktop"
!else if "${APP_ID}" == "com.readyset.rdst-desktop.cl"
  !define RDST_INSTANCE "cl"
  !define RDST_DATA_DIR_NAME ".rdst-cl"
  !define RDST_ELECTRON_NAME "rdst-desktop-cl"
!else if "${APP_ID}" == "com.readyset.rdst-desktop.staging"
  !define RDST_INSTANCE "staging"
  !define RDST_DATA_DIR_NAME ".rdst-staging"
  !define RDST_ELECTRON_NAME "rdst-desktop-staging"
!else
  !error "installer.nsh has no uninstall cleanup for APP_ID ${APP_ID}"
!endif
!ifdef RDST_INSTANCE
  !define RDST_CLEANUP_ARGS " -Instance ${RDST_INSTANCE}"
!else
  !define RDST_CLEANUP_ARGS ""
!endif

!macro customUnInstall
  ${ifNot} ${isUpdated}
    ; The sidecar's data directory: RDST_DATA_DIR when set, otherwise %USERPROFILE%\.rdst
    ; (.rdst-<instance> for a flavor build).
    nsExec::Exec `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\uninstall-cleanup.ps1"${RDST_CLEANUP_ARGS}`
    Pop $R0
    ${if} $R0 != 0
      DetailPrint "RDST credential/custom-directory cleanup failed: $R0"
      SetErrorLevel 1
    ${endIf}
    ReadEnvStr $R0 "RDST_DATA_DIR"
    ${if} $R0 == ""
      ReadEnvStr $R0 "USERPROFILE"
      ${if} $R0 != ""
        RMDir /r "$R0\${RDST_DATA_DIR_NAME}"
      ${endIf}
    ${endIf}

    ; Electron's profile and the updater's download cache. The uninstaller's own
    ; deleteAppDataOnUninstall covers the first two; the rest guards a renamed product.
    RMDir /r "$APPDATA\${RDST_ELECTRON_NAME}"
    RMDir /r "$APPDATA\${PRODUCT_NAME}"
    RMDir /r "$LOCALAPPDATA\${RDST_ELECTRON_NAME}-updater"

    ; Start at login. Electron names the Run value after the AppUserModelID.
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APP_ID}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${PRODUCT_NAME}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${RDST_ELECTRON_NAME}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${APP_ID}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${PRODUCT_NAME}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${RDST_ELECTRON_NAME}"

  ${endIf}
!macroend
