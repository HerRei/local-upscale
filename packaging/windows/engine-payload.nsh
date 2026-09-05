; The small manifest is embedded in the installer. Payload hashes are therefore
; covered by the installer's signature when production signing is enabled.
!macro NSIS_HOOK_POSTINSTALL
  DetailPrint "Verifying and installing the CUDA inference engine..."
  nsExec::ExecToLog '"$INSTDIR\localsr-next.exe" --install-engine "$INSTDIR\engine-payload.json" "$EXEDIR" "$INSTDIR\engine"'
  Pop $0
  ${If} $0 != 0
    SetErrorLevel 1
    MessageBox MB_OK|MB_ICONSTOP "The CUDA engine could not be installed. Keep every .engine.tar.gz.part file beside this installer and retry. See the installation details for the error." /SD IDOK
    Abort
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  RMDir /r "$INSTDIR\engine"
!macroend
