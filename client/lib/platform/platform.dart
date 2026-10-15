import 'dart:io';

/// Windows, macOS and Linux builds share the app but not the phone-only parts (saving songs on the device).
final bool isDesktop = Platform.isWindows || Platform.isLinux || Platform.isMacOS;
