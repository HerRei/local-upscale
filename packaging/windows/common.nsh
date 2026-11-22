; Shared NSIS UI text and uninstaller cleanup for every LocalSR Windows edition.
!define MUI_WELCOMEPAGE_TITLE "Welcome to ${PRODUCTNAME} ${VERSION} Setup"
!define MUI_FINISHPAGE_TITLE "Completing ${PRODUCTNAME} ${VERSION} Setup"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${PRODUCTNAME}"
!define MUI_UNCONFIRMPAGE_TEXT_TOP "This will uninstall ${PRODUCTNAME} ${VERSION} from your computer. Downloaded models and settings are kept unless you check the option below."

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    DeleteRegKey HKCU "Software\Classes\*\shell\LocalSRNext"
    DeleteRegKey HKCU "Software\Classes\Directory\shell\LocalSRNext"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSRNext.Active"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSRNext.Quick"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSRNext.Best"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell\LocalSRNext.Picker"
  ${EndIf}
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    SetShellVarContext current
    RMDir /r "$LOCALAPPDATA\LocalSR"
  ${EndIf}
!macroend
