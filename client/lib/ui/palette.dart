import 'package:flutter/material.dart';

/// A colour theme. The app reads the current one through the k* getters below.
class AppPalette {
  const AppPalette(this.id, this.name, this.accent, this.bg, this.card, this.nav, this.muted, {this.light = false});
  final String id;
  final String name;
  final Color accent;
  final Color bg;
  final Color card;
  final Color nav;
  final Color muted;
  final bool light;

  Color get text => light ? const Color(0xFF15151C) : Colors.white;
  Color get onAccent => light ? Colors.white : Colors.black;
}

const List<AppPalette> palettes = [
  AppPalette('emerald', 'Emerald', Color(0xFF1ED760), Color(0xFF0C0C10), Color(0xFF181820), Color(0xFF111116), Color(0xFF9A9AA8)),
  AppPalette('midnight', 'Midnight', Color(0xFF5AA9FF), Color(0xFF0A0F1C), Color(0xFF141C30), Color(0xFF0E1424), Color(0xFF94A0BA)),
  AppPalette('grape', 'Grape', Color(0xFFB388FF), Color(0xFF120D1C), Color(0xFF1E1630), Color(0xFF160F24), Color(0xFFA598BD)),
  AppPalette('sunset', 'Sunset', Color(0xFFFF8A3D), Color(0xFF15100C), Color(0xFF241A14), Color(0xFF1B130E), Color(0xFFB1A294)),
  AppPalette('rose', 'Rose', Color(0xFFFF5C8A), Color(0xFF160C10), Color(0xFF25141B), Color(0xFF1C0F15), Color(0xFFB59AA4)),
  AppPalette('amoled', 'AMOLED', Color(0xFF1ED760), Color(0xFF000000), Color(0xFF111111), Color(0xFF000000), Color(0xFF8A8A8A)),
  AppPalette('light', 'Light', Color(0xFF12873A), Color(0xFFF5F5F8), Color(0xFFFFFFFF), Color(0xFFFFFFFF), Color(0xFF6A6A78), light: true),
];

AppPalette _current = palettes.first;

AppPalette get palette => _current;

AppPalette paletteById(String id) => palettes.firstWhere((p) => p.id == id, orElse: () => palettes.first);

void applyPalette(String id) => _current = paletteById(id);

Color get kAccent => _current.accent;
Color get kBg => _current.bg;
Color get kCard => _current.card;
Color get kNav => _current.nav;
Color get kMuted => _current.muted;
Color get kText => _current.text;
Color get kOnAccent => _current.onAccent;
Color get kFaint => _current.muted.withValues(alpha: 0.25);

/// Widgets that read colours straight from the palette do not notice a theme change by themselves: rebuild everything.
void rebuildAll(BuildContext context) {
  void visit(Element e) {
    e.markNeedsBuild();
    e.visitChildren(visit);
  }

  (context as Element).visitChildren(visit);
  WidgetsBinding.instance.rootElement?.visitChildren(visit);
}
