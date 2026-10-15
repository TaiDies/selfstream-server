import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Small per-device preferences.
class Settings extends ChangeNotifier {
  Settings(this._p);
  final SharedPreferences _p;

  /// 0 = original quality, otherwise the highest kbit/s the server may send (it transcodes library songs).
  int get maxBitRate => _p.getInt('maxBitRate') ?? 0;
  set maxBitRate(int v) {
    _p.setInt('maxBitRate', v);
    notifyListeners();
  }

  /// Report what you listen to, for Stats and Discover.
  bool get trackListening => _p.getBool('trackListening') ?? true;
  set trackListening(bool v) {
    _p.setBool('trackListening', v);
    notifyListeners();
  }

  /// Listens that could not be sent yet (no connection); sent with the next one.
  List<Map<String, dynamic>> get pendingPlays {
    final raw = _p.getString('pendingPlays');
    if (raw == null) return [];
    try {
      return (jsonDecode(raw) as List).map((e) => Map<String, dynamic>.from(e as Map)).toList();
    } catch (_) {
      return [];
    }
  }

  set pendingPlays(List<Map<String, dynamic>> v) {
    final trimmed = v.length > 500 ? v.sublist(v.length - 500) : v;
    _p.setString('pendingPlays', jsonEncode(trimmed));
  }
}
