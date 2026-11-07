; Inno Setup script for the Windows installer.
;
; Built by packaging/windows/build_installer.ps1, which passes the version
; and paths; can also be compiled by hand from this folder:
;   iscc /DAppVersion=0.2.0 ytdlmanager.iss
;
; Installs per user by default (no administrator rights needed; the user can
; choose "all users" in the first dialog), adds a Start menu entry and an
; optional desktop shortcut, and registers an uninstaller. Uninstalling
; leaves the user's settings and history in %APPDATA%\YTDLManager.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\YTDLManager"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif

[Setup]
AppId={{8C1B5E2A-3F4D-4B6E-9A7C-2D5F1E0B9C43}
AppName=YTDL Manager
AppVersion={#AppVersion}
AppVerName=YTDL Manager {#AppVersion}
AppPublisher=YTDL Manager
AppPublisherURL=https://github.com/Azeezhamzat/YTDL_Manager
DefaultDirName={autopf}\YTDL Manager
DefaultGroupName=YTDL Manager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=YTDLManager-{#AppVersion}-windows-x64-setup
SetupIconFile=..\icons\YTDLManager.ico
UninstallDisplayIcon={app}\YTDLManager.exe
UninstallDisplayName=YTDL Manager
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\YTDL Manager"; Filename: "{app}\YTDLManager.exe"
Name: "{autodesktop}\YTDL Manager"; Filename: "{app}\YTDLManager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\YTDLManager.exe"; Description: "{cm:LaunchProgram,YTDL Manager}"; Flags: nowait postinstall skipifsilent
