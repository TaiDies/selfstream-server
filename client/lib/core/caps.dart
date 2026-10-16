// What the connected server offers. No Flutter imports: shared by every platform.

class TabSpec {
  const TabSpec({required this.id, required this.title, required this.icon, required this.kind, this.screen = ''});
  final String id;
  final String title;
  final String icon;

  /// 'screen' (drawn from server data), 'search', 'library' or 'nativehome' (plain Navidrome)
  final String kind;
  final String screen;

  factory TabSpec.fromJson(Map<String, dynamic> m) => TabSpec(
        id: '${m['id'] ?? ''}',
        title: '${m['title'] ?? ''}',
        icon: '${m['icon'] ?? ''}',
        kind: '${m['kind'] ?? ''}',
        screen: '${m['screen'] ?? ''}',
      );
}

class Capabilities {
  const Capabilities({required this.features, required this.tabs, this.strings = const {}, this.name = '', this.fromServer = false});
  final Set<String> features;
  final List<TabSpec> tabs;
  final Map<String, String> strings;
  final String name;

  /// False for a plain Navidrome (or any Subsonic) server that is not running selfstream.
  final bool fromServer;

  bool has(String feature) => features.contains(feature);
  String text(String key, String fallback) => strings[key] ?? fallback;

  /// A server without selfstream: the music player parts only.
  static const Capabilities generic = Capabilities(
    features: {},
    tabs: [
      TabSpec(id: 'home', title: 'Home', icon: 'home', kind: 'nativehome'),
      TabSpec(id: 'search', title: 'Search', icon: 'search', kind: 'search'),
      TabSpec(id: 'library', title: 'Library', icon: 'library', kind: 'library'),
    ],
  );

  factory Capabilities.fromJson(Map<String, dynamic> m) {
    final tabs = (m['tabs'] as List? ?? []).whereType<Map>().map((t) => TabSpec.fromJson(Map<String, dynamic>.from(t))).where((t) => t.id.isNotEmpty).toList();
    final strings = <String, String>{};
    final raw = m['strings'];
    if (raw is Map) raw.forEach((k, v) => strings['$k'] = '$v');
    return Capabilities(
      features: {for (final f in (m['features'] as List? ?? [])) '$f'},
      tabs: tabs.isEmpty ? generic.tabs : tabs,
      strings: strings,
      name: '${m['name'] ?? ''}',
      fromServer: true,
    );
  }
}

/// A screen the server described: a title, header buttons and a list of sections.
class ScreenData {
  ScreenData(this.json);
  final Map<String, dynamic> json;
  String get title => '${json['title'] ?? ''}';
  bool get settings => json['settings'] == true;
  List<Map<String, dynamic>> get actions => _maps(json['actions']);
  List<Map<String, dynamic>> get sections => _maps(json['sections']);
}

List<Map<String, dynamic>> _maps(Object? v) =>
    v is List ? v.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList() : <Map<String, dynamic>>[];
