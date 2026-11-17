; Inno Setup script. Build:  iscc /DAppVersion=1.0.0 packaging\installer.iss   (after PyInstaller)
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
[Setup]
AppId={{B0757D10-5E1D-4B7B-9C25-5B07B0757D10}
AppName=Bot Forge Studio
AppVersion={#AppVersion}
AppPublisher=Comet UX Labs
DefaultDirName={autopf}\Bot Forge Studio
DefaultGroupName=Bot Forge Studio
OutputDir=..\dist
OutputBaseFilename=BotForgeStudio-{#AppVersion}-Windows11-Setup
SetupIconFile=botstudio.ico
UninstallDisplayIcon={app}\BotStudio.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
PrivilegesRequiredOverridesAllowed=dialog
[Files]
Source: "..\dist\BotStudio\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
[Icons]
Name: "{group}\Bot Forge Studio"; Filename: "{app}\BotStudio.exe"
Name: "{autodesktop}\Bot Forge Studio"; Filename: "{app}\BotStudio.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"
[Registry]
Root: HKA; Subkey: "Software\Classes\.bstudio"; ValueType: string; ValueData: "BotStudio.Project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\BotStudio.Project\shell\open\command"; ValueType: string; ValueData: """{app}\BotStudio.exe"" ""%1"""; Flags: uninsdeletekey
[Run]
Filename: "{app}\BotStudio.exe"; Description: "Launch Bot Forge Studio"; Flags: nowait postinstall skipifsilent
