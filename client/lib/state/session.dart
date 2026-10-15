import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/api.dart';
import 'library.dart';
import 'offline.dart';
import 'player.dart';
import 'settings.dart';

/// Everything that exists while someone is signed in.
class Services {
  Services(this.api, this.settings)
      : offline = OfflineStore(api, settings),
        library = Library(api) {
    player = PlayerController(api, library, offline, settings);
  }

  final Api api;
  final Settings settings;
  final OfflineStore offline;
  final Library library;
  late final PlayerController player;

  Future<void> start() async {
    await offline.load();
    await library.refresh();
    library.startPolling();
    await player.restore();
  }

  void dispose() {
    player.dispose();
    library.dispose();
    api.close();
  }
}

/// Server address + login, kept on the device. Signing in creates [services].
class Session extends ChangeNotifier {
  SharedPreferences? _prefs;
  bool loaded = false;
  String server = '';
  String user = '';
  String _password = '';
  Services? services;
  late Settings settings;

  bool get signedIn => services != null;

  Future<void> load() async {
    _prefs = await SharedPreferences.getInstance();
    settings = Settings(_prefs!);
    server = _prefs!.getString('server') ?? '';
    user = _prefs!.getString('user') ?? '';
    _password = _prefs!.getString('password') ?? '';
    if (server.isNotEmpty && user.isNotEmpty && _password.isNotEmpty) {
      try {
        await _enter(server, user, _password, verify: false);
      } catch (_) {
        // stay on the login screen with the saved address filled in
      }
    }
    loaded = true;
    notifyListeners();
  }

  /// Throws [ApiException] with a message fit to show.
  Future<void> signIn(String serverInput, String userInput, String password) async {
    final s = normalizeServer(serverInput);
    if (s.isEmpty || userInput.trim().isEmpty || password.isEmpty) {
      throw ApiException('Fill in the server address, username and password.');
    }
    await _enter(s, userInput.trim(), password, verify: true);
    await _prefs!.setString('server', s);
    await _prefs!.setString('user', userInput.trim());
    await _prefs!.setString('password', password);
    server = s;
    user = userInput.trim();
    _password = password;
    notifyListeners();
  }

  Future<void> _enter(String s, String u, String p, {required bool verify}) async {
    final api = Api(server: s, user: u, password: p);
    if (verify) {
      try {
        await api.checkLogin();
      } catch (_) {
        api.close();
        rethrow;
      }
    }
    services?.dispose();
    final svc = Services(api, settings);
    services = svc;
    notifyListeners();
    // the first refresh must not block the UI (and must not log you out when the server is briefly away)
    svc.start();
  }

  @override
  void dispose() {
    services?.dispose();
    super.dispose();
  }

  Future<void> signOut() async {
    services?.dispose();
    services = null;
    _password = '';
    await _prefs?.remove('password');
    notifyListeners();
  }
}
