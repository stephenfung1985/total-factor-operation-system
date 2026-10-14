<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tfos.backup</string>
  <key>ProgramArguments</key><array><string>__ROOT__/deploy/backup.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>__DATA_DIR__/logs/backup.log</string>
  <key>StandardErrorPath</key><string>__DATA_DIR__/logs/backup.log</string>
</dict></plist>
