import 'package:flutter/material.dart';
import 'package:window_manager/window_manager.dart';

/// The desktop window: pinned on top and the small "mini player" shape.
class DesktopWindow extends ChangeNotifier {
  bool mini = false;
  bool pinned = false;
  bool lyrics = false; // the mini player can grow a lyrics area
  Size _restore = const Size(1100, 720);

  static const Size _normalMin = Size(760, 520);
  static const Size _miniSize = Size(480, 230);
  static const Size _miniLyricsSize = Size(480, 520);

  static Future<void> init() async {
    await windowManager.ensureInitialized();
    const options = WindowOptions(size: Size(1100, 720), minimumSize: _normalMin, center: true, title: 'Selfstream');
    await windowManager.waitUntilReadyToShow(options, () async {
      await windowManager.show();
      await windowManager.focus();
    });
  }

  Future<void> togglePin() async {
    pinned = !pinned;
    await windowManager.setAlwaysOnTop(pinned);
    notifyListeners();
  }

  Future<void> toggleLyrics() async {
    lyrics = !lyrics;
    notifyListeners();
    if (mini) await windowManager.setSize(lyrics ? _miniLyricsSize : _miniSize);
  }

  Future<void> toggleMini() async {
    if (!mini) {
      _restore = await windowManager.getSize();
      mini = true;
      notifyListeners();
      await windowManager.setMinimumSize(const Size(380, 200));
      await windowManager.setSize(lyrics ? _miniLyricsSize : _miniSize);
    } else {
      mini = false;
      notifyListeners();
      await windowManager.setMinimumSize(_normalMin);
      await windowManager.setSize(_restore);
    }
  }
}

final DesktopWindow desktopWindow = DesktopWindow();
