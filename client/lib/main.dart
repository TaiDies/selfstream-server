import 'package:flutter/material.dart';
import 'package:just_audio_background/just_audio_background.dart';
import 'package:provider/provider.dart';

import 'core/api.dart';
import 'state/library.dart';
import 'state/offline.dart';
import 'state/player.dart';
import 'state/session.dart';
import 'state/settings.dart';
import 'ui/login_screen.dart';
import 'ui/shell.dart';
import 'ui/widgets.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await JustAudioBackground.init(
    androidNotificationChannelId: 'dev.selfstream.audio',
    androidNotificationChannelName: 'Selfstream playback',
    androidNotificationOngoing: true,
  );
  runApp(const SelfstreamApp());
}

class SelfstreamApp extends StatefulWidget {
  const SelfstreamApp({super.key});
  @override
  State<SelfstreamApp> createState() => _SelfstreamAppState();
}

class _SelfstreamAppState extends State<SelfstreamApp> {
  final Session session = Session();

  @override
  void initState() {
    super.initState();
    session.load();
  }

  @override
  void dispose() {
    session.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider<Session>.value(
      value: session,
      child: Consumer<Session>(
        builder: (context, s, _) {
          if (s.loaded) applyPalette(s.settings.themeId);
          final Widget home;
          if (!s.loaded) {
            home = const Scaffold(body: Center(child: CircularProgressIndicator()));
          } else if (s.signedIn) {
            home = const Shell();
          } else {
            home = const LoginScreen();
          }
          return MaterialApp(
            title: 'Selfstream',
            debugShowCheckedModeBanner: false,
            theme: buildTheme(),
            // providers go above the Navigator so every pushed page can use them
            builder: (context, child) {
              final svc = s.services;
              if (svc == null) return child ?? const SizedBox.shrink();
              return MultiProvider(
                key: ValueKey(svc.hashCode),
                providers: [
                  Provider<Api>.value(value: svc.api),
                  ChangeNotifierProvider<Settings>.value(value: svc.settings),
                  ChangeNotifierProvider<Library>.value(value: svc.library),
                  ChangeNotifierProvider<OfflineStore>.value(value: svc.offline),
                  ChangeNotifierProvider<PlayerController>.value(value: svc.player),
                ],
                child: child ?? const SizedBox.shrink(),
              );
            },
            home: home,
          );
        },
      ),
    );
  }
}
