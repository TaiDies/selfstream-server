import 'package:flutter/foundation.dart';

import '../core/caps.dart';

/// The connected server's capabilities, shared with every screen. Starts as the plain-server set until the server answers.
class CapsHolder extends ChangeNotifier {
  Capabilities value = Capabilities.generic;
  bool loaded = false;

  bool has(String feature) => value.has(feature);

  void set(Capabilities c) {
    value = c;
    loaded = true;
    notifyListeners();
  }
}
