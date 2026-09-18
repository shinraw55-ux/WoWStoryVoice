#define MyAppName "WoW Story Voice"
#define MyAppVersion "0.8.0"
#define MyAppExeName "WoWStoryVoice.exe"

[Setup]
AppId={{B54A99D4-9D59-4C25-AF76-0CE7F0FEA5AF}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=WoWStoryVoice
DefaultDirName={localappdata}\Programs\WoW Story Voice
DefaultGroupName=WoW Story Voice
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=WoWStoryVoice-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\dist\WoWStoryVoice\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\WoWStoryVoice-Addon.zip"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\WoW Story Voice"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\WoW Story Voice"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch WoW Story Voice"; Flags: nowait postinstall skipifsilent
