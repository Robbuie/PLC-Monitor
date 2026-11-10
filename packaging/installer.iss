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
; A log in %TEMP% ("Setup Log yyyy-mm-dd #nnn.txt") for every run, and the
; updater asks for one beside the staged installer: an update that fails
; silently can then be read rather than guessed at.
SetupLogging=yes

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

[Code]
{ Every running copy of PLC Monitor -- the window, a headless runner, the
  Windows service -- holds one of these mutexes (app/instance.py).

  [InstallDelete] removes the old _internal folder before the new files are
  copied. If a running copy still has files open, the copy fails half way and
  leaves a program that cannot start ("Failed to start embedded python
  interpreter!" -- 1.5.1 on a PC where that happened). So nothing is touched
  until no copy is running: the window that launched this update is usually
  gone within a second or two, and this waits up to 30 s for it. A service
  does not go away by itself; then setup stops, having changed nothing, and
  says what to do. }
const
  RunningMutexes = 'PLCMonitorRunning,Global\PLCMonitorRunning';

function InitializeSetup(): Boolean;
var
  Waited: Integer;
begin
  Waited := 0;
  while CheckForMutexes(RunningMutexes) and (Waited < 60) do
  begin
    Sleep(500);
    Waited := Waited + 1;
  end;
  Result := not CheckForMutexes(RunningMutexes);
  if not Result then
  begin
    Log('PLC Monitor is still running; setup stopped before changing anything.');
    SuppressibleMsgBox('PLC Monitor is still running: its window, a headless runner, ' +
      'or the Windows service.' + #13#10#13#10 +
      'Close it -- for the service, Stop on the Service page -- and run setup again. ' +
      'Nothing has been changed.', mbError, MB_OK, IDOK);
  end;
end;

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\PLC Monitor\updates"
Type: filesandordirs; Name: "{localappdata}\PLC Monitor\cache"
