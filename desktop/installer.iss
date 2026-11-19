; Inno Setup script - builds ERROR-HUB-DOWNLODER-Setup.exe (installs for the current user, no admin needed)
#ifndef AppVer
  #define AppVer "1.0"
#endif

[Setup]
AppId={{6F3C2A1E-8B4D-4C6A-9E21-5D7A0B3C9F18}
AppName=ERROR HUB DOWNLODER
AppVersion={#AppVer}
AppPublisher=ERROR HUB
DefaultDirName={autopf}\ERROR HUB DOWNLODER
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\installer
OutputBaseFilename=ERROR-HUB-DOWNLODER-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\ERROR-HUB-DOWNLODER.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\ERROR-HUB-DOWNLODER\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ERROR HUB DOWNLODER"; Filename: "{app}\ERROR-HUB-DOWNLODER.exe"
Name: "{autodesktop}\ERROR HUB DOWNLODER"; Filename: "{app}\ERROR-HUB-DOWNLODER.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ERROR-HUB-DOWNLODER.exe"; Description: "Open ERROR HUB DOWNLODER"; Flags: nowait postinstall skipifsilent
