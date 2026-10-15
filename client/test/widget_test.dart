import 'package:flutter_test/flutter_test.dart';
import 'package:selfstream/main.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('starts on the login screen', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const SelfstreamApp());
    await tester.pumpAndSettle();
    expect(find.text('Selfstream'), findsOneWidget);
    expect(find.text('Sign in'), findsOneWidget);
  });
}
