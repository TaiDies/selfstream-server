import 'package:flutter/material.dart';

import '../core/models.dart';
import 'album_screen.dart';
import 'widgets.dart';

class StatsScreen extends StatefulWidget {
  const StatsScreen({super.key});
  @override
  State<StatsScreen> createState() => _StatsScreenState();
}

class _StatsScreenState extends State<StatsScreen> with AutomaticKeepAliveClientMixin {
  static const ranges = <(String, String)>[('week', '7 days'), ('month', '30 days'), ('year', 'Year'), ('all', 'All time')];
  String range = 'month';
  Stats? stats;
  String? error;
  bool loading = false;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final s = await context.api.stats(range);
      if (mounted) setState(() => stats = s);
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    }
    if (mounted) setState(() => loading = false);
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final s = stats;
    return Scaffold(
      appBar: AppBar(title: const Text('Your activity', style: TextStyle(fontWeight: FontWeight.w800))),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(padding: const EdgeInsets.only(bottom: 32), children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
            child: Wrap(spacing: 8, children: [
              for (final r in ranges)
                ChoiceChip(
                  label: Text(r.$2),
                  selected: range == r.$1,
                  onSelected: (_) {
                    setState(() => range = r.$1);
                    _load();
                  },
                ),
            ]),
          ),
          if (loading && s == null) const Padding(padding: EdgeInsets.all(40), child: Center(child: CircularProgressIndicator())),
          if (error != null && s == null) EmptyNote(error!, icon: Icons.error_outline),
          if (s != null) ..._content(context, s),
        ]),
      ),
    );
  }

  List<Widget> _content(BuildContext context, Stats s) {
    if (s.plays == 0 && s.totalSeconds == 0) {
      return const [EmptyNote('No listening recorded in this period yet. Play some music and come back.', icon: Icons.insights)];
    }
    final hours = s.totalSeconds >= 3600 ? s.totalHours.toStringAsFixed(1) : '${(s.totalSeconds / 60).round()}';
    final unit = s.totalSeconds >= 3600 ? 'hours listened' : 'minutes listened';
    final maxGenre = s.topGenres.isEmpty ? 1 : s.topGenres.map((g) => g.seconds).reduce((a, b) => a > b ? a : b);
    final maxArtist = s.topArtists.isEmpty ? 1 : s.topArtists.first.seconds;
    return [
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16),
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(hours, style: const TextStyle(fontSize: 56, fontWeight: FontWeight.w900, color: kAccent, height: 1)),
              Text(unit, style: const TextStyle(color: kMuted, fontSize: 16)),
              const SizedBox(height: 4),
              Text('${s.lifetimeHours.toStringAsFixed(1)} hours with Selfstream in total', style: const TextStyle(color: kMuted, fontSize: 12)),
            ]),
          ),
        ),
      ),
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
        child: GridView.count(
          crossAxisCount: 3,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 8,
          crossAxisSpacing: 8,
          childAspectRatio: 1.35,
          children: [
            _Stat('${s.plays}', 'plays'),
            _Stat('${s.uniqueTracks}', 'different songs'),
            _Stat('${s.uniqueArtists}', 'artists'),
            _Stat('${s.newArtists}', 'new artists'),
            _Stat('${s.currentStreak}', 'day streak'),
            _Stat('${s.avgMinutesPerDay.round()}m', 'per day'),
          ],
        ),
      ),
      if (s.byHour.isNotEmpty && s.totalSeconds > 0) ...[
        const SectionHeader('When you listen'),
        _Bars(title: 'Day of week', values: s.byWeekday, labels: const ['M', 'T', 'W', 'T', 'F', 'S', 'S']),
        _Bars(title: 'Time of day', values: s.byHour, labels: [for (var h = 0; h < 24; h++) h % 6 == 0 ? '$h' : '']),
      ],
      if (s.series.length > 1) ...[
        const SectionHeader('Over time'),
        _Bars(title: '', values: s.series.map((e) => e.value).toList(), labels: [for (var i = 0; i < s.series.length; i++) i % 7 == 0 ? s.series[i].key.split('-').skip(1).join('/') : '']),
      ],
      if (s.topTracks.isNotEmpty) ...[
        const SectionHeader('Top songs'),
        for (var i = 0; i < s.topTracks.length && i < 10; i++)
          SongTile(
            song: s.topTracks[i].key,
            number: i + 1,
            onTap: () => context.player.playSongs(s.topTracks.map((e) => e.key).toList(), start: i),
            menuExtras: const [],
          ),
      ],
      if (s.topArtists.isNotEmpty) ...[
        const SectionHeader('Top artists'),
        for (var i = 0; i < s.topArtists.length && i < 10; i++)
          _RankRow(
            rank: i + 1,
            title: s.topArtists[i].name,
            subtitle: '${fmtListening(s.topArtists[i].seconds)} · ${s.topArtists[i].plays} plays',
            fraction: s.topArtists[i].seconds / maxArtist,
            leading: Cover(s.topArtists[i].coverArt, size: 44, circle: true),
          ),
      ],
      if (s.topGenres.isNotEmpty) ...[
        const SectionHeader('Top genres'),
        for (var i = 0; i < s.topGenres.length && i < 8; i++)
          _RankRow(
            rank: i + 1,
            title: s.topGenres[i].name,
            subtitle: fmtListening(s.topGenres[i].seconds),
            fraction: s.topGenres[i].seconds / maxGenre,
          ),
        const Padding(
          padding: EdgeInsets.fromLTRB(16, 4, 16, 0),
          child: Text('Genres come from your library tags, Deezer and MusicBrainz, looked up in the background. New songs fill in over time.', style: TextStyle(color: kMuted, fontSize: 11)),
        ),
      ] else
        const Padding(
          padding: EdgeInsets.fromLTRB(16, 16, 16, 0),
          child: Text('Genres will appear here once the server has looked up the music you play.', style: TextStyle(color: kMuted)),
        ),
      if (s.topAlbums.isNotEmpty) ...[
        const SectionHeader('Top albums'),
        HRow(children: [
          for (final a in s.topAlbums.take(10))
            CardTile(
              coverId: a.coverArt,
              title: a.name,
              subtitle: a.artist,
              onTap: a.albumId.isEmpty ? null : () => context.open(AlbumScreen(albumId: a.albumId, name: a.name)),
            ),
        ]),
      ],
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
        child: Text(
          'Skip rate ${(s.skipRate * 100).round()}% · longest streak ${s.longestStreak} days · ${s.activeDays} active days',
          style: const TextStyle(color: kMuted),
        ),
      ),
    ];
  }
}

class _Stat extends StatelessWidget {
  const _Stat(this.value, this.label);
  final String value;
  final String label;
  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.start, children: [
            FittedBox(fit: BoxFit.scaleDown, child: Text(value, style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w800))),
            Text(label, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: kMuted, fontSize: 12)),
          ]),
        ),
      );
}

class _Bars extends StatelessWidget {
  const _Bars({required this.title, required this.values, required this.labels});
  final String title;
  final List<int> values;
  final List<String> labels;

  @override
  Widget build(BuildContext context) {
    final peak = values.isEmpty ? 1 : values.reduce((a, b) => a > b ? a : b);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (title.isNotEmpty) Padding(padding: const EdgeInsets.only(bottom: 6), child: Text(title, style: const TextStyle(color: kMuted, fontSize: 12))),
        SizedBox(
          height: 90,
          child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
            for (var i = 0; i < values.length; i++)
              Expanded(
                child: Padding(
                  padding: EdgeInsets.symmetric(horizontal: values.length > 40 ? 0.3 : 1.5),
                  child: Container(
                    height: peak == 0 ? 2.0 : (86 * values[i] / peak).clamp(2.0, 86.0).toDouble(),
                    decoration: BoxDecoration(color: values[i] == peak && peak > 0 ? kAccent : kAccent.withValues(alpha: 0.45), borderRadius: BorderRadius.circular(2)),
                  ),
                ),
              ),
          ]),
        ),
        const SizedBox(height: 4),
        Row(children: [
          for (var i = 0; i < values.length; i++)
            Expanded(child: Text(i < labels.length ? labels[i] : '', textAlign: TextAlign.center, maxLines: 1, overflow: TextOverflow.visible, softWrap: false, style: const TextStyle(color: kMuted, fontSize: 9))),
        ]),
      ]),
    );
  }
}

class _RankRow extends StatelessWidget {
  const _RankRow({required this.rank, required this.title, required this.subtitle, required this.fraction, this.leading});
  final int rank;
  final String title;
  final String subtitle;
  final double fraction;
  final Widget? leading;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        child: Row(children: [
          SizedBox(width: 26, child: Text('$rank', style: const TextStyle(color: kMuted, fontWeight: FontWeight.w700))),
          if (leading != null) ...[leading!, const SizedBox(width: 12)],
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
              const SizedBox(height: 4),
              ClipRRect(
                borderRadius: BorderRadius.circular(3),
                child: LinearProgressIndicator(value: fraction.isNaN ? 0.02 : fraction.clamp(0.02, 1.0).toDouble(), minHeight: 5, backgroundColor: Colors.white10),
              ),
              const SizedBox(height: 3),
              Text(subtitle, style: const TextStyle(color: kMuted, fontSize: 12)),
            ]),
          ),
        ]),
      );
}
