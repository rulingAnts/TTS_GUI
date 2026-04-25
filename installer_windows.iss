; installer_windows.iss — Inno Setup script for Kokoro TTS Studio
; Compile with: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer_windows.iss

#define MyAppName      "Kokoro TTS Studio"
#define MyAppVersion   "1.0"
#define MyAppPublisher "Seth Johnston"
#define MyAppURL       "https://github.com/rulingAnts/TTS_GUI"
#define MyAppExeName   "Kokoro TTS Studio.exe"
#define MySourceDir    "dist\Kokoro TTS Studio"
#define MyOutputDir    "dist"
#define MyOutputBase   "KokoroTTSStudio-Setup"

[Setup]
AppId={{A3F7B2C1-4D8E-4F2A-9B6C-1E5D7A8F0B3C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\KokoroTTSStudio
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Require at least Windows 10
MinVersion=10.0
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyOutputBase}
SetupIconFile=assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Don't require admin rights — install per-user if non-admin
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64os

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
  GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Bundle all PyInstaller output
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}";      Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up user data only if the user agrees (leave model weights alone)
Type: dirifempty; Name: "{app}"

[Code]
// Show a note about first-run model download
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    MsgBox(
      'Kokoro TTS Studio is installed!' + #13#10 + #13#10 +
      'On first launch the app will download Kokoro model weights ' +
      '(~330 MB) from Hugging Face. An internet connection is required ' +
      'for this one-time setup.' + #13#10 + #13#10 +
      'Subsequent launches work fully offline.',
      mbInformation, MB_OK
    );
  end;
end;
