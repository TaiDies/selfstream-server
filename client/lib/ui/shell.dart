import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api.dart';
import '../state/player.dart';
import 'discover_screen.dart';
import 'home_screen.dart';
import 'library_screen.dart';
import 'search_screen.dart';
import 'stats_screen.dart';
import 'widgets.dart';

class Shell extends StatefulWidget {
  const Shell({super.key});
  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int tab = 0;
  StreamSubscription<String>? errors;

  static const _items = <(IconData, IconData, String)>[
    (Icons.home_outlined, Icons.home, 'Home'),
    (Icons.search, Icons.search, 'Search'),
    (Icons.library_music_outlined, Icons.library_music, 'Library'),
    (Icons.auto_awesome_outlined, Icons.auto_awesome, 'Discover'),
    (Icons.insights_outlined, Icons.insights, 'Activity'),
  ];

  @override
  void initState() {
    super.initState();
    errors = context.read<PlayerController>().errors.listen((m) {
      if (mounted) context.toast(m);
    });
  }

  @override
  void dispose() {
    errors?.cancel();
    super.dispose();
  }

  void goTo(int i) => setState(() => tab = i);

  @override
  Widget build(BuildContext context) {
    final pages = <Widget>[
      HomeScreen(onOpenTab: goTo),
      const SearchScreen(),
      const LibraryScreen(),
      const DiscoverScreen(),
      const StatsScreen(),
    ];
    final api = context.read<Api>();
    final banner = StreamBuilder<bool>(
      stream: api.offlineChanges,
      initialData: api.offline,
      builder: (_, snap) => (snap.data ?? false)
          ? Container(
              width: double.infinity,
              color: kCard,
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
              child: SafeArea(
                bottom: false,
                child: Row(children: [
                  Icon(Icons.cloud_off, size: 16, color: kMuted),
                  const SizedBox(width: 8),
                  Expanded(child: Text('Offline: showing saved pages. Downloaded songs still play.', style: TextStyle(color: kMuted, fontSize: 12))),
                ]),
              ),
            )
          : const SizedBox.shrink(),
    );
    final body = Column(children: [banner, Expanded(child: IndexedStack(index: tab, children: pages))]);
    return LayoutBuilder(builder: (context, c) {
      final wide = c.maxWidth >= 800;
      if (wide) {
        return Scaffold(
          body: Row(children: [
            NavigationRail(
              selectedIndex: tab,
              onDestinationSelected: goTo,
              labelType: NavigationRailLabelType.all,
              backgroundColor: kNav,
              destinations: [
                for (final i in _items) NavigationRailDestination(icon: Icon(i.$1), selectedIcon: Icon(i.$2), label: Text(i.$3)),
              ],
            ),
            Expanded(child: body),
          ]),
          bottomNavigationBar: const MiniPlayer(),
        );
      }
      return Scaffold(
        body: body,
        bottomNavigationBar: Column(mainAxisSize: MainAxisSize.min, children: [
          const MiniPlayer(),
          NavigationBar(
            selectedIndex: tab,
            onDestinationSelected: goTo,
            destinations: [
              for (final i in _items) NavigationDestination(icon: Icon(i.$1), selectedIcon: Icon(i.$2), label: i.$3),
            ],
          ),
        ]),
      );
    });
  }
}
