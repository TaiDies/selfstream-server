import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api.dart';
import '../platform/platform.dart';
import '../state/desktop_window.dart';
import '../state/player.dart';
import '../core/caps.dart';
import '../state/caps_holder.dart';
import 'library_screen.dart';
import 'mini_window.dart';
import 'search_screen.dart';
import 'server_screen.dart';
import 'widgets.dart';

class Shell extends StatefulWidget {
  const Shell({super.key});
  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int tab = 0;
  StreamSubscription<String>? errors;

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

  List<TabSpec> _tabs = Capabilities.generic.tabs;

  void goTo(int i) => setState(() => tab = i);

  /// Switch to a tab by the id the server gave it.
  void goToId(String id) {
    final i = _tabs.indexWhere((t) => t.id == id);
    if (i >= 0) goTo(i);
  }

  Widget _page(TabSpec t) {
    switch (t.kind) {
      case 'search':
        return const SearchScreen();
      case 'library':
        return const LibraryScreen();
      case 'nativehome':
        return ServerScreen(key: ValueKey('page-${t.id}'), screenId: t.id, title: t.title, onOpenTab: goToId, loader: nativeHome);
      default:
        return ServerScreen(key: ValueKey('page-${t.id}-${t.screen}'), screenId: t.screen, title: t.title, onOpenTab: goToId);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!isDesktop) return _full(context);
    // on a computer the window can shrink to a small player; the app keeps running underneath
    return ListenableBuilder(
      listenable: desktopWindow,
      builder: (context, _) => desktopWindow.mini ? const MiniWindow() : _full(context),
    );
  }

  Widget _full(BuildContext context) {
    final caps = context.watch<CapsHolder>();
    if (!caps.loaded) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    _tabs = caps.value.tabs;
    if (tab >= _tabs.length) tab = 0;
    final pages = [for (final t in _tabs) _page(t)];
    final items = [for (final t in _tabs) (tabIconsFor(t.icon).$1, tabIconsFor(t.icon).$2, t.title)];
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
              trailing: !isDesktop
                  ? null
                  : Expanded(
                      child: Align(
                        alignment: Alignment.bottomCenter,
                        child: ListenableBuilder(
                          listenable: desktopWindow,
                          builder: (context, _) => Padding(
                            padding: const EdgeInsets.only(bottom: 12),
                            child: Column(mainAxisSize: MainAxisSize.min, children: [
                              IconButton(
                                tooltip: desktopWindow.pinned ? 'Unpin from top' : 'Keep on top',
                                icon: Icon(desktopWindow.pinned ? Icons.push_pin : Icons.push_pin_outlined, color: desktopWindow.pinned ? kAccent : kMuted),
                                onPressed: desktopWindow.togglePin,
                              ),
                              IconButton(
                                tooltip: 'Mini player',
                                icon: Icon(Icons.picture_in_picture_alt_outlined, color: kMuted),
                                onPressed: desktopWindow.toggleMini,
                              ),
                            ]),
                          ),
                        ),
                      ),
                    ),
              destinations: [
                for (final i in items) NavigationRailDestination(icon: Icon(i.$1), selectedIcon: Icon(i.$2), label: Text(i.$3)),
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
              for (final i in items) NavigationDestination(icon: Icon(i.$1), selectedIcon: Icon(i.$2), label: i.$3),
            ],
          ),
        ]),
      );
    });
  }
}
