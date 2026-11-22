; Match the standard installer default for a new installation.
!define MULTIUSER_INSTALLMODE_DEFAULT_CURRENTUSER
!include "${__FILEDIR__}\common.nsh"
; The small manifest is embedded in the installer. Payload hashes are therefore
; covered by the installer's signature when production signing is enabled.
!include "FileFunc.nsh"
Var LocalSREngineManaged
!macro NSIS_HOOK_POSTINSTALL
  ${GetOptions} $CMDLINE "/LOCALSR_ENGINE_MANAGED=" $LocalSREngineManaged
  ${If} $LocalSREngineManaged == "1"
    DetailPrint "Using the updater's verified versioned inference engine."
    ; The flag does not prove ownership of an existing engine at the install
    ; destination. Keep it: the host uses the verified versioned engine instead.
  ${Else}
  DetailPrint "Verifying and installing the CUDA inference engine..."
  nsExec::ExecToStack '"$INSTDIR\localsr-next.exe" --install-engine "$INSTDIR\engine-payload.json" "$EXEDIR" "$INSTDIR\engine"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    SetErrorLevel 1
    MessageBox MB_OK|MB_ICONSTOP "The CUDA engine could not be installed: $1$\r$\n$\r$\nPut all engine parts beside this installer and retry. Download them at https://herrei.github.io/localsr/download/" /SD IDOK
    Abort
  ${EndIf}
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  RMDir /r "$INSTDIR\engine"
!macroend
