import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/models.dart';
import '../state/caps_holder.dart';
import '../state/settings.dart';
import 'album_screen.dart';
import 'artist_screen.dart';
import 'remote_playlist_screen.dart';
import 'widgets.dart';

class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});
  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final TextEditingController ctl = TextEditingController();
  final FocusNode focus = FocusNode();
  Timer? _debounce;
  SearchResults? results;
  List<String> suggestions = [];
  bool showResults = false;
  bool loading = false;
  bool allSongs = false;
  String? error;
  int _ticket = 0;
  int _suggestTicket = 0;

  @override
  void dispose() {
    _debounce?.cancel();
    ctl.dispose();
    focus.dispose();
    super.dispose();
  }

  void _typed(String q) {
    _debounce?.cancel();
    setState(() {
      showResults = false;
      if (q.trim().length < 2) suggestions = [];
    });
    if (q.trim().length < 2) return;
    _debounce = Timer(const Duration(milliseconds: 220), () => _loadSuggestions(q.trim()));
  }

  Future<void> _loadSuggestions(String q) async {
    final ticket = ++_suggestTicket;
    try {
      if (!context.has('suggest')) return;
      final s = await context.api.suggestions(q);
      if (!mounted || ticket != _suggestTicket) return;
      setState(() => suggestions = s);
    } catch (_) {
      // suggestions are a nicety; ignore failures
    }
  }

  Future<void> _submit(String raw) async {
    final q = raw.trim();
    if (q.length < 2) return;
    ctl.text = q;
    ctl.selection = TextSelection.collapsed(offset: q.length);
    focus.unfocus();
    context.read<Settings>().addRecentSearch(q);
    final ticket = ++_ticket;
    setState(() {
      showResults = true;
      loading = true;
      allSongs = false;
      error = null;
    });
    try {
      final r = await context.api.search(q);
      if (!mounted || ticket != _ticket) return;
      setState(() {
        results = r;
        loading = false;
      });
    } catch (e) {
      if (!mounted || ticket != _ticket) return;
      setState(() {
        error = e.toString();
        loading = false;
      });
    }
  }

  void _fill(String text) {
    ctl.text = text;
    ctl.selection = TextSelection.collapsed(offset: text.length);
    _typed(text);
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<Settings>();
    final text = ctl.text.trim();
    Widget body;
    if (showResults) {
      body = _results();
    } else if (text.length < 2) {
      body = _recents(settings);
    } else {
      body = _suggestions(settings, text);
    }
    return Scaffold(
      appBar: AppBar(
        title: TextField(
          controller: ctl,
          focusNode: focus,
          onChanged: _typed,
          onSubmitted: _submit,
          textInputAction: TextInputAction.search,
          decoration: InputDecoration(
            hintText: 'Songs, artists, albums, playlists',
            border: InputBorder.none,
            prefixIcon: const Icon(Icons.search),
            suffixIcon: ctl.text.isEmpty
                ? null
                : IconButton(
                    icon: const Icon(Icons.close),
                    onPressed: () {
                      ctl.clear();
                      _typed('');
                      setState(() => results = null);
                      focus.requestFocus();
                    },
                  ),
          ),
        ),
        bottom: loading ? const PreferredSize(preferredSize: Size.fromHeight(2), child: LinearProgressIndicator(minHeight: 2)) : null,
      ),
      body: body,
    );
  }

  Widget _recents(Settings settings) {
    final recents = settings.recentSearches;
    if (recents.isEmpty) {
      return EmptyNote(context.read<CapsHolder>().value.text('searchHint', 'Search your library.'), icon: Icons.search);
    }
    return ListView(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 8, 0),
        child: Row(children: [
          const Expanded(child: Text('Recent searches', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700))),
          TextButton(onPressed: settings.clearRecentSearches, child: const Text('Clear')),
        ]),
      ),
      for (final q in recents)
        ListTile(
          dense: true,
          leading: Icon(Icons.history, color: kMuted),
          title: Text(q, maxLines: 1, overflow: TextOverflow.ellipsis),
          trailing: IconButton(icon: Icon(Icons.close, size: 18, color: kMuted), onPressed: () => settings.removeRecentSearch(q)),
          onTap: () => _submit(q),
        ),
    ]);
  }

  Widget _suggestions(Settings settings, String text) {
    final lower = text.toLowerCase();
    final fromHistory = settings.recentSearches.where((r) => r.toLowerCase().contains(lower) && r.toLowerCase() != lower).take(3).toList();
    final fromServer = suggestions.where((s) => !fromHistory.contains(s)).toList();
    return ListView(children: [
      ListTile(
        leading: Icon(Icons.search, color: kAccent),
        title: Text('Search for "$text"'),
        onTap: () => _submit(text),
      ),
      for (final s in fromHistory)
        ListTile(
          dense: true,
          leading: Icon(Icons.history, color: kMuted),
          title: Text(s, maxLines: 1, overflow: TextOverflow.ellipsis),
          trailing: IconButton(icon: Icon(Icons.north_west, size: 18, color: kMuted), onPressed: () => _fill(s)),
          onTap: () => _submit(s),
        ),
      for (final s in fromServer)
        ListTile(
          dense: true,
          leading: Icon(Icons.search, color: kMuted),
          title: Text(s, maxLines: 1, overflow: TextOverflow.ellipsis),
          trailing: IconButton(icon: Icon(Icons.north_west, size: 18, color: kMuted), onPressed: () => _fill(s)),
          onTap: () => _submit(s),
        ),
    ]);
  }

  Widget _results() {
    final r = results;
    if (error != null) return EmptyNote(error!, icon: Icons.error_outline);
    if (r == null) return const SizedBox.shrink();
    if (r.songs.isEmpty && r.artists.isEmpty && r.albums.isEmpty && r.playlists.isEmpty) return const EmptyNote('Nothing found');
    final shown = allSongs ? r.songs : r.songs.take(6).toList();
    return ListView(children: [
      if (r.artists.isNotEmpty) ...[
        const SectionHeader('Artists'),
        HRow(height: 160, children: [
          for (final a in r.artists)
            CardTile(
              coverId: a.coverArt,
              title: a.name,
              circle: true,
              width: 112,
              onTap: () => context.open(ArtistScreen(artistId: a.id, name: a.name)),
            ),
        ]),
      ],
      if (r.songs.isNotEmpty) ...[
        const SectionHeader('Songs'),
        for (var i = 0; i < shown.length; i++) SongTile(song: shown[i], onTap: () => context.player.playSongs(r.songs, start: i)),
        if (!allSongs && r.songs.length > shown.length)
          TextButton(onPressed: () => setState(() => allSongs = true), child: Text('Show all ${r.songs.length} songs')),
      ],
      if (r.albums.isNotEmpty) ...[
        const SectionHeader('Albums'),
        for (final a in r.albums)
          ListTile(
            leading: Cover(a.coverArt, size: 52, radius: 6),
            title: Text(a.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
            subtitle: Text(['Album', if (a.artist.isNotEmpty) a.artist, if (a.year > 0) '${a.year}'].join(' · '), maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: kMuted)),
            trailing: Icon(Icons.chevron_right, color: kMuted),
            onTap: () => context.open(AlbumScreen(albumId: a.id, name: a.name)),
          ),
      ],
      if (r.playlists.isNotEmpty) ...[
        const SectionHeader('Playlists'),
        for (final p in r.playlists)
          ListTile(
            leading: const Icon(Icons.queue_music, size: 32),
            title: Text(p.title, maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Text(p.author, style: TextStyle(color: kMuted)),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => context.open(RemotePlaylistScreen(playlistId: p.id, name: p.title, author: p.author)),
          ),
      ],
      const SizedBox(height: 24),
    ]);
  }
}
