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

  String get themeId => _p.getString('themeId') ?? 'emerald';
  set themeId(String v) {
    _p.setString('themeId', v);
    notifyListeners();
  }

  List<String> get recentSearches => List<String>.of(_p.getStringList('recentSearches') ?? const <String>[]);

  void addRecentSearch(String q) {
    final t = q.trim();
    if (t.length < 2) return;
    final list = recentSearches..removeWhere((e) => e.toLowerCase() == t.toLowerCase());
    list.insert(0, t);
    _p.setStringList('recentSearches', list.take(30).toList());
    notifyListeners();
  }

  void removeRecentSearch(String q) {
    _p.setStringList('recentSearches', recentSearches..remove(q));
    notifyListeners();
  }

  void clearRecentSearches() {
    _p.remove('recentSearches');
    notifyListeners();
  }

  /// Liking a YouTube song also downloads it into the server library (off: the like is only remembered).
  bool get likeDownloadsToServer => _p.getBool('likeDownloadsToServer') ?? false;
  set likeDownloadsToServer(bool v) {
    _p.setBool('likeDownloadsToServer', v);
    notifyListeners();
  }

  /// Computer volume, 0..1 (phones use their own volume keys).
  double get volume => (_p.getDouble('volume') ?? 1.0).clamp(0.0, 1.0);
  set volume(double v) {
    _p.setDouble('volume', v.clamp(0.0, 1.0));
  }

  /// Windows: show what is playing on Discord. The id is optional, the server can supply one.
  bool get discordPresence => _p.getBool('discordPresence') ?? true;
  set discordPresence(bool v) {
    _p.setBool('discordPresence', v);
    notifyListeners();
  }

  String get discordClientId => _p.getString('discordClientId') ?? '';
  set discordClientId(String v) {
    _p.setString('discordClientId', v.trim());
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
