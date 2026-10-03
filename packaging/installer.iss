; Inno Setup script for PLC Monitor. Needs Inno Setup 6.3 or newer.
; Compiled by packaging/build.py, which passes the version in:
;
;     iscc /DAppVersion=1.0.0 packaging\installer.iss
;
; Per user, into %LOCALAPPDATA%\Programs, like File Manager and NetControl: no
; UAC prompt in front of every update. PrivilegesRequiredOverridesAllowed lets a
; site install it for all users instead.
;
; The monitor configuration and the logs are not in {app}: they live in
; %PROGRAMDATA%\PLC Monitor, shared with the Windows service, and an uninstall
; leaves them alone -- they are the customer's data, not the program's.
;
; The output name has no spaces, because GitHub renames an asset with spaces and
; the URL in latest.json would then point at nothing.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "PLC Monitor"
#define AppExe "PLCMonitor.exe"
#define Publisher "Robbuie"
#define RepoUrl "https://github.com/Robbuie/plc-monitor"

[Setup]
; Never change this GUID. It is how an install is recognised as an upgrade, and
; it is copied into app/core/updates.py as APP_ID, where the updater reads the
; install location from the uninstall key Inno names after it.
AppId={{3D752E85-572B-48DF-906D-89C68C5F385B}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
VersionInfoVersion={#AppVersion}

DefaultDirName={autopf}\PLCMonitor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=..\dist
OutputBaseFilename=PLCMonitor-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; The frozen folder is replaced whole, so a file a release stopped shipping
; does not stay behind through every update after it.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\PLCMonitor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; A service pointing at an exe that is about to be deleted fails at every boot.
; Removing it needs an administrator; if the uninstall is not elevated this
; does nothing and the Service page's Remove is the way.
Filename: "{sys}\sc.exe"; Parameters: "stop PLCMonitorService"; Flags: runhidden; RunOnceId: "StopService"
Filename: "{sys}\sc.exe"; Parameters: "delete PLCMonitorService"; Flags: runhidden; RunOnceId: "DeleteService"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\PLC Monitor\updates"
Type: filesandordirs; Name: "{localappdata}\PLC Monitor\cache"
