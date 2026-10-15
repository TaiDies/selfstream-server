import 'package:flutter_test/flutter_test.dart';
import 'package:selfstream/main.dart';

void main() {
  testWidgets('app starts', (tester) async {
    await tester.pumpWidget(const SelfstreamApp());
    expect(find.text('Selfstream'), findsOneWidget);
  });
}
